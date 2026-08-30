"use client";

import React, { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
    AlertCircle,
    AlertTriangle,
    BarChart3,
    Building2,
    Calendar,
    CheckCircle,
    ChevronDown,
    ChevronUp,
    Clock,
    DollarSign,
    Download,
    Edit2,
    Eye,
    FileText,
    Info,
    Layers,
    Plus,
    RefreshCw,
    Save,
    Trash2,
    X,
} from "lucide-react";

import { useAuth } from "../../../contexts/AuthContext";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../../components/ui/table";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Input } from "../../../components/ui/input";
import { Label } from "../../../components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../../components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from "../../../components/ui/select";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "../../../components/ui/dialog";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger, } from "../../../components/ui/tooltip";
import { Textarea } from "../../../components/ui/textarea";

import {formatMoneyFromDollars} from '@/lib/currency';
// ─── Constants ────────────────────────────────────────────────────────────────

const MGMT_ROLES = ["super_admin", "strata_manager", "ec_member"];
const DEL_ROLES = ["super_admin", "strata_manager"];

const ALLOC_TYPES = [
    {value: "all_lots", label: "All Lots"},
    {value: "class_a", label: "Class A Only"},
    {value: "class_b", label: "Class B Only"},
    {value: "common", label: "Common (Split)"},
];

const FINANCIAL_YEARS = ["2026-2027", "2025-2026", "2024-2025"];
const DEFAULT_FY = "2026-2027";
// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: aud
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const aud = (n) => formatMoneyFromDollars(n);
/**
 * @generated FunctionHeader
 * Function: fmtDate
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmtDate = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleDateString("en-AU", {day: "2-digit", month: "2-digit", year: "numeric"});
};
/**
 * @generated FunctionHeader
 * Function: isDateFuture
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const isDateFuture = (iso) => {
    if (!iso) return false;
    return new Date(iso) > new Date();
};
/**
 * @generated FunctionHeader
 * Function: todayIso
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const todayIso = () => new Date().toISOString().slice(0, 10);
/**
 * @generated FunctionHeader
 * Function: classStatus
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function classStatus(cls) {
    if (!cls) return null;
    if (!cls.is_active) return "inactive";
    if (isDateFuture(cls.effective_from)) return "pending";
    return "active";
}
/**
 * @generated FunctionHeader
 * Function: StatusBadge
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StatusBadge({status}) {
    const map = {
        active: {label: "Active", className: "bg-emerald-100 text-emerald-800 border-emerald-200"},
        inactive: {label: "Inactive", className: "bg-slate-100 text-slate-600 border-slate-200"},
        pending: {label: "Pending", className: "bg-amber-100 text-amber-800 border-amber-200"},
    };
    const {label, className} = map[ status ] || map.inactive;
    return <Badge variant="outline" className={className}>{label}</Badge>;
}
/**
 * @generated FunctionHeader
 * Function: FundBadge
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function FundBadge({fund}) {
    if (fund === "sinking")
        return <Badge variant="outline" className="bg-purple-100 text-purple-800 border-purple-200">Sinking</Badge>;
    return <Badge variant="outline" className="bg-blue-100 text-blue-800 border-blue-200">Admin</Badge>;
}
/**
 * @generated FunctionHeader
 * Function: AllocBadge
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function AllocBadge({type}) {
    const map = {
        all_lots: "bg-slate-100 text-slate-700",
        class_a: "bg-sky-100 text-sky-800",
        class_b: "bg-teal-100 text-teal-800",
        common: "bg-violet-100 text-violet-800",
    };
    const label = ALLOC_TYPES.find((a) => a.value === type)?.label ?? type;
    return <Badge variant="outline" className={map[ type ] || "bg-slate-100 text-slate-700"}>{label}</Badge>;
}
// Parse space-or-comma separated strings into arrays
/**
 * @generated FunctionHeader
 * Function: parseList
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const parseList = (str) =>
    str ? str.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean) : [];
// Export array of objects to CSV download
/**
 * @generated FunctionHeader
 * Function: exportToCsv
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function exportToCsv(rows, filename) {
    if (!rows.length) return;
    const headers = Object.keys(rows[ 0 ]);
    const csv = [
        headers.join(","),
        ...rows.map((r) =>
            headers.map((h) => {
                const v = r[ h ] ?? "";
                return typeof v === "string" && v.includes(",") ? `"${v}"` : v;
            }).join(",")
        ),
    ].join("\n");
    const blob = new Blob([csv], {type: "text/csv"});
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}
// ─── Sub-component: Info Banner ───────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: LegalInfoBanner
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function LegalInfoBanner() {
    const [collapsed, setCollapsed] = useState(false);
    return (
        <Card className="border-amber-300 bg-amber-50">
            <CardHeader className="pb-2 cursor-pointer" onClick={() => setCollapsed((c) => !c)}>
                <CardTitle className="text-sm flex items-center gap-2 text-amber-900">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0"/>
                    Legal Notice — Class A / Class B Scheme Splits (ACT Unit Titles Act 2001)
                    <span className="ml-auto text-amber-700">{collapsed ? <ChevronDown className="w-4 h-4"/> :
                        <ChevronUp className="w-4 h-4"/>}</span>
                </CardTitle>
            </CardHeader>
            {!collapsed && (
                <CardContent className="text-xs text-amber-900 space-y-1.5 pt-0">
                    <p>
                        Class A / Class B splits are a <strong>legal concept</strong> under the{" "}
                        <em>ACT Unit Titles (Management) Act 2011</em> and the{" "}
                        <em>Unit Titles Act 2001</em>. A Class A/B scheme divides an owners
                        corporation into two separate classes of lot ownership, each with
                        separate levy obligations and voting entitlements for class-specific matters.
                    </p>
                    <p>
                        <strong>Any activation of a class split requires formal registration</strong> with
                        the ACT Land Titles Office and must be approved by special resolution at a general
                        meeting (75% majority). This tool <em>models</em> the financial impact only —
                        it does not create, register, or legally effect a scheme split.
                    </p>
                    <p className="font-semibold">
                        Consult your licensed strata manager and/or solicitor before making any decisions
                        based on the projections shown here.
                    </p>
                </CardContent>
            )}
        </Card>
    );
}
// ─── Sub-component: Split Status Card ────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: SplitStatusCard
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function SplitStatusCard({status, onPreview, previewLoading}) {
    if (!status) return null;
    const {split_status, effective_from, class_a, class_b, total_uoe} = status;

    const statusMap = {
        active: {label: "Split active", icon: CheckCircle, color: "text-emerald-700 bg-emerald-50 border-emerald-200"},
        pending: {label: "Split pending activation", icon: Clock, color: "text-amber-700 bg-amber-50 border-amber-200"},
        not_active: {
            label: "Split not yet active",
            icon: AlertCircle,
            color: "text-slate-600 bg-slate-50 border-slate-200"
        },
        not_configured: {label: "No split configured", icon: Info, color: "text-slate-500 bg-white border-slate-200"},
    };

    const {label, icon: Icon, color} = statusMap[ split_status ] || statusMap.not_configured;

    return (
        <Card className={`border ${color.split(" ").slice(1).join(" ")}`}>
            <CardHeader className="pb-3">
                <div className="flex items-center justify-between flex-wrap gap-2">
                    <CardTitle className={`text-sm flex items-center gap-2 ${color.split(" ")[ 0 ]}`}>
                        <Icon className="w-4 h-4"/>
                        {label}
                        {effective_from && split_status !== "not_configured" && (
                            <span className="font-normal text-muted-foreground ml-1">
                — effective {fmtDate(effective_from)}
              </span>
                        )}
                    </CardTitle>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={onPreview}
                        disabled={previewLoading || split_status === "not_configured"}
                        className="text-xs"
                    >
                        <Eye className="w-3.5 h-3.5 mr-1.5"/>
                        {previewLoading ? "Loading…" : "Preview Levy Impact"}
                    </Button>
                </div>
            </CardHeader>
            {( class_a || class_b ) && (
                <CardContent className="pt-0">
                    <div className="grid grid-cols-3 gap-3 text-sm">
                        {class_a && (
                            <div className="rounded-lg bg-sky-50 border border-sky-200 p-3 text-center">
                                <div className="text-xs text-sky-600 font-medium uppercase tracking-wide mb-0.5">Class
                                    A
                                </div>
                                <div className="font-bold text-sky-900">{class_a.name}</div>
                                <div className="text-xs text-muted-foreground mt-1">
                                    {class_a.unit_count} units · {class_a.total_uoe} UOE
                                </div>
                            </div>
                        )}
                        {class_b && (
                            <div className="rounded-lg bg-teal-50 border border-teal-200 p-3 text-center">
                                <div className="text-xs text-teal-600 font-medium uppercase tracking-wide mb-0.5">Class
                                    B
                                </div>
                                <div className="font-bold text-teal-900">{class_b.name}</div>
                                <div className="text-xs text-muted-foreground mt-1">
                                    {class_b.unit_count} units · {class_b.total_uoe} UOE
                                </div>
                            </div>
                        )}
                        <div className="rounded-lg bg-slate-50 border border-slate-200 p-3 text-center">
                            <div className="text-xs text-slate-500 font-medium uppercase tracking-wide mb-0.5">Total
                                Building
                            </div>
                            <div className="font-bold text-slate-800">{total_uoe} UOE</div>
                            <div className="text-xs text-muted-foreground mt-1">
                                {( class_a?.unit_count ?? 0 ) + ( class_b?.unit_count ?? 0 )} units assigned
                            </div>
                        </div>
                    </div>
                </CardContent>
            )}
        </Card>
    );
}
// ─── Sub-component: Class Card ────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: ClassCard
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ClassCard({cls, classLabel, canEdit, canDelete, onEdit, onDelete, loading}) {
    const [showAll, setShowAll] = useState(false);
    const status = classStatus(cls);
    const units = cls?.unit_numbers ?? [];
    const visible = showAll ? units : units.slice(0, 10);
    const hidden = units.length - 10;

    const labelColor = classLabel === "A"
        ? "bg-sky-100 text-sky-800 border-sky-200"
        : "bg-teal-100 text-teal-800 border-teal-200";
    const borderColor = classLabel === "A" ? "border-sky-200" : "border-teal-200";

    if (!cls) {
        return (
            <Card
                className={`border-2 border-dashed ${borderColor} flex flex-col items-center justify-center min-h-[260px] gap-4 p-8 text-center`}>
                <div className={`rounded-full p-3 ${classLabel === "A" ? "bg-sky-50" : "bg-teal-50"}`}>
                    <Building2 className={`w-8 h-8 ${classLabel === "A" ? "text-sky-400" : "text-teal-400"}`}/>
                </div>
                <div>
                    <p className="font-semibold text-slate-700 mb-1">Class {classLabel} not defined</p>
                    <p className="text-xs text-muted-foreground">
                        Define which lots belong to Class {classLabel} and their entitlement share.
                    </p>
                </div>
                {canEdit && (
                    <Button variant="outline" size="sm" onClick={onEdit} disabled={loading}>
                        <Plus className="w-4 h-4 mr-1.5"/>
                        Define Class {classLabel}
                    </Button>
                )}
            </Card>
        );
    }

    return (
        <Card className={`border-2 ${borderColor}`}>
            <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-2">
                    <div className="space-y-1">
                        <div className="flex items-center gap-2 flex-wrap">
                            <CardTitle className="text-base">{cls.class_name}</CardTitle>
                            <Badge variant="outline" className={labelColor}>Class {classLabel}</Badge>
                            <StatusBadge status={status}/>
                        </div>
                        {cls.description && (
                            <CardDescription className="text-xs">{cls.description}</CardDescription>
                        )}
                    </div>
                    <div className="flex gap-1 flex-shrink-0">
                        {canEdit && (
                            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onEdit} disabled={loading}>
                                <Edit2 className="w-3.5 h-3.5"/>
                            </Button>
                        )}
                        {canDelete && (
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 text-destructive hover:text-destructive"
                                onClick={onDelete}
                                disabled={loading}
                            >
                                <Trash2 className="w-3.5 h-3.5"/>
                            </Button>
                        )}
                    </div>
                </div>
            </CardHeader>

            <CardContent className="space-y-3">
                {/* Stats row */}
                <div className="grid grid-cols-2 gap-2 text-sm">
                    <div className="rounded-md bg-slate-50 border p-2.5 text-center">
                        <div className="text-xs text-muted-foreground mb-0.5">Units</div>
                        <div className="font-bold">{units.length}</div>
                    </div>
                    <div className="rounded-md bg-slate-50 border p-2.5 text-center">
                        <div className="text-xs text-muted-foreground mb-0.5">Total UOE</div>
                        <div className="font-bold">{cls.total_uoe ?? "—"}</div>
                    </div>
                </div>

                {/* Effective from */}
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Calendar className="w-3.5 h-3.5"/>
                    Effective from: <span className="font-medium text-foreground">{fmtDate(cls.effective_from)}</span>
                </div>

                {/* Unit list */}
                {units.length > 0 && (
                    <div className="space-y-1.5">
                        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                            Assigned Units ({units.length})
                        </p>
                        <div className="flex flex-wrap gap-1">
                            {visible.map((u) => (
                                <Badge key={u} variant="secondary" className="text-xs font-mono">{u}</Badge>
                            ))}
                            {!showAll && hidden > 0 && (
                                <button
                                    className="text-xs text-primary underline underline-offset-2 hover:no-underline"
                                    onClick={() => setShowAll(true)}
                                >
                                    +{hidden} more
                                </button>
                            )}
                            {showAll && hidden > 0 && (
                                <button
                                    className="text-xs text-primary underline underline-offset-2 hover:no-underline"
                                    onClick={() => setShowAll(false)}
                                >
                                    show less
                                </button>
                            )}
                        </div>
                    </div>
                )}

                {cls.notes && (
                    <p className="text-xs text-muted-foreground italic border-t pt-2">{cls.notes}</p>
                )}
            </CardContent>
        </Card>
    );
}

// ─── Sub-component: Create/Edit Dialog ───────────────────────────────────────

const EMPTY_FORM = {
    class_name: "",
    description: "",
    unit_type_prefixes: "",
    unit_numbers: "",
    effective_from: todayIso(),
    notes: "",
};
/**
 * @generated FunctionHeader
 * Function: ClassDialog
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ClassDialog({open, onClose, classLabel, existing, onSave, saving}) {
    const [form, setForm] = useState(EMPTY_FORM);
    const isEdit = !!existing?._id;

    useEffect(() => {
        if (open) {
            setForm(
                existing
                    ? {
                        class_name: existing.class_name ?? "",
                        description: existing.description ?? "",
                        unit_type_prefixes: ( existing.unit_type_prefixes ?? [] ).join(", "),
                        unit_numbers: ( existing.unit_numbers ?? [] ).join(", "),
                        effective_from: existing.effective_from?.slice(0, 10) ?? todayIso(),
                        notes: existing.notes ?? "",
                    }
                    : EMPTY_FORM
            );
        }
    }, [open, existing]);
    /**
     * @generated FunctionHeader
     * Function: set
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const set = (k, v) => setForm((f) => ( {...f, [ k ]: v} ));

    const pastEffective =
        isEdit && form.effective_from && new Date(form.effective_from) < new Date(todayIso());
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = (e) => {
        e.preventDefault();
        if (!form.class_name.trim()) {
            toast.error("Class name is required.");
            return;
        }
        if (!form.effective_from) {
            toast.error("Effective from date is required.");
            return;
        }
        if (!isEdit && new Date(form.effective_from) < new Date(todayIso())) {
            toast.error("Effective date must be today or in the future for a new class.");
            return;
        }
        onSave({
            class_name: form.class_name.trim(),
            description: form.description.trim(),
            unit_type_prefixes: parseList(form.unit_type_prefixes),
            unit_numbers: parseList(form.unit_numbers),
            effective_from: form.effective_from,
            notes: form.notes.trim(),
            class_label: classLabel === "A" ? "class_a" : "class_b",
        });
    };

    const labelColor = classLabel === "A" ? "text-sky-700" : "text-teal-700";

    return (
        <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle className={`flex items-center gap-2 ${labelColor}`}>
                        <Layers className="w-5 h-5"/>
                        {isEdit ? "Edit" : "Define"} Class {classLabel}
                    </DialogTitle>
                    <DialogDescription>
                        {isEdit
                            ? "Update the class definition. Changes will not be legally effective until registered."
                            : "Define which lots form Class " + classLabel + " of the scheme split."}
                    </DialogDescription>
                </DialogHeader>

                <form onSubmit={handleSubmit} className="space-y-4">
                    <div className="space-y-1.5">
                        <Label htmlFor="class_name">
                            Class Name <span className="text-destructive">*</span>
                        </Label>
                        <Input
                            id="class_name"
                            placeholder='e.g. "Apartments" or "Townhouses"'
                            value={form.class_name}
                            onChange={(e) => set("class_name", e.target.value)}
                            required
                        />
                    </div>

                    <div className="space-y-1.5">
                        <Label htmlFor="description">Description</Label>
                        <Textarea
                            id="description"
                            placeholder="Optional description of this class"
                            value={form.description}
                            onChange={(e) => set("description", e.target.value)}
                            rows={2}
                        />
                    </div>

                    <div className="space-y-1.5">
                        <Label htmlFor="unit_type_prefixes">
                            Unit Type Prefixes
                            <TooltipProvider>
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <Info className="w-3.5 h-3.5 ml-1 inline text-muted-foreground cursor-help"/>
                                    </TooltipTrigger>
                                    <TooltipContent className="max-w-56 text-xs">
                                        Comma-separated prefixes (e.g. "UA" to auto-assign all units starting with
                                        "UA").
                                        This auto-populates unit numbers from the unit register on save.
                                    </TooltipContent>
                                </Tooltip>
                            </TooltipProvider>
                        </Label>
                        <Input
                            id="unit_type_prefixes"
                            placeholder='e.g. "UA, UB" — auto-assigns matching units'
                            value={form.unit_type_prefixes}
                            onChange={(e) => set("unit_type_prefixes", e.target.value)}
                        />
                    </div>

                    <div className="space-y-1.5">
                        <Label htmlFor="unit_numbers">
                            Unit Numbers Override
                            <span className="text-xs text-muted-foreground ml-1">(space or comma separated)</span>
                        </Label>
                        <Textarea
                            id="unit_numbers"
                            placeholder="e.g. UA1, UA2, UA3 — overrides prefix auto-assignment"
                            value={form.unit_numbers}
                            onChange={(e) => set("unit_numbers", e.target.value)}
                            rows={3}
                        />
                    </div>

                    <div className="space-y-1.5">
                        <Label htmlFor="effective_from">
                            Effective From <span className="text-destructive">*</span>
                        </Label>
                        <Input
                            id="effective_from"
                            type="date"
                            value={form.effective_from}
                            min={isEdit ? undefined : todayIso()}
                            onChange={(e) => set("effective_from", e.target.value)}
                            required
                        />
                        {pastEffective && (
                            <p className="text-xs text-amber-600 flex items-center gap-1">
                                <AlertTriangle className="w-3 h-3"/>
                                Warning: effective date is in the past. This may imply a retroactive change.
                            </p>
                        )}
                    </div>

                    <div className="space-y-1.5">
                        <Label htmlFor="notes">Notes</Label>
                        <Textarea
                            id="notes"
                            placeholder="Any additional notes (e.g. resolution reference, ACTLTO registration number)"
                            value={form.notes}
                            onChange={(e) => set("notes", e.target.value)}
                            rows={2}
                        />
                    </div>

                    <DialogFooter>
                        <Button type="button" variant="outline" onClick={onClose} disabled={saving}>
                            Cancel
                        </Button>
                        <Button type="submit" disabled={saving}>
                            {saving ? (
                                <><RefreshCw className="w-4 h-4 mr-2 animate-spin"/>Saving…</>
                            ) : (
                                <><Save className="w-4 h-4 mr-2"/>{isEdit ? "Update Class" : "Create Class"}</>
                            )}
                        </Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}
// ─── Sub-component: Levy Impact Preview Dialog ────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: LevyImpactDialog
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function LevyImpactDialog({open, onClose, data, financialYear}) {
    const rows = data?.units ?? [];
    /**
     * @generated FunctionHeader
     * Function: handleExport
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleExport = () => {
        if (!rows.length) return;
        exportToCsv(
            rows.map((r) => ( {
                Unit: r.unit_number,
                Class: r.scheme_class,
                "Current Levy": r.current_levy,
                "Projected Levy": r.projected_levy,
                "Change ($)": r.delta,
                "Change (%)": r.delta_pct,
            } )),
            `levy-impact-${financialYear}.csv`
        );
    };

    const classATotal = data?.class_a_total_levy ?? rows.filter((r) => r.scheme_class === "class_a").reduce((s, r) => s + ( r.projected_levy ?? 0 ), 0);
    const classBTotal = data?.class_b_total_levy ?? rows.filter((r) => r.scheme_class === "class_b").reduce((s, r) => s + ( r.projected_levy ?? 0 ), 0);
    const grandTotal = data?.grand_total_levy ?? ( classATotal + classBTotal );

    return (
        <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
            <DialogContent className="max-w-4xl max-h-[85vh] flex flex-col">
                <DialogHeader className="flex-shrink-0">
                    <DialogTitle className="flex items-center gap-2">
                        <BarChart3 className="w-5 h-5 text-primary"/>
                        Levy Impact Preview — {financialYear}
                    </DialogTitle>
                    <DialogDescription>
                        Projected levy amounts under the proposed Class A / Class B split.
                        Red = unit pays more; Green = unit pays less. Figures are indicative only.
                    </DialogDescription>
                </DialogHeader>

                <div className="flex-1 overflow-auto">
                    {rows.length === 0 ? (
                        <div className="text-center py-12 text-muted-foreground">
                            <BarChart3 className="w-10 h-10 mx-auto mb-3 opacity-30"/>
                            <p>No impact data available. Ensure both classes are defined.</p>
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Unit</TableHead>
                                    <TableHead>Class</TableHead>
                                    <TableHead className="text-right">Current Levy</TableHead>
                                    <TableHead className="text-right">Projected Levy</TableHead>
                                    <TableHead className="text-right">Change ($)</TableHead>
                                    <TableHead className="text-right">Change (%)</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {rows.map((r) => {
                                    const delta = r.change_amount ?? 0;
                                    const pctVal = r.change_pct ?? 0;
                                    const positive = delta > 0.005;
                                    const negative = delta < -0.005;
                                    return (
                                        <TableRow
                                            key={r.unit_number}
                                            className={positive ? "bg-red-50" : negative ? "bg-green-50" : ""}
                                        >
                                            <TableCell className="font-mono font-medium">{r.unit_number}</TableCell>
                                            <TableCell>
                                                <Badge
                                                    variant="outline"
                                                    className={
                                                        r.scheme_class === "class_a"
                                                            ? "bg-sky-50 text-sky-700 border-sky-200"
                                                            : "bg-teal-50 text-teal-700 border-teal-200"
                                                    }
                                                >
                                                    {r.scheme_class === "class_a" ? "Class A" : "Class B"}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="text-right">{aud(r.current_levy)}</TableCell>
                                            <TableCell
                                                className="text-right font-medium">{aud(r.projected_levy)}</TableCell>
                                            <TableCell
                                                className={`text-right font-semibold ${positive ? "text-red-600" : negative ? "text-green-600" : "text-muted-foreground"}`}>
                                                {positive && "+"}{aud(delta)}
                                            </TableCell>
                                            <TableCell
                                                className={`text-right font-semibold ${positive ? "text-red-600" : negative ? "text-green-600" : "text-muted-foreground"}`}>
                                                {delta > 0.005 ? "+" : ""}{pctVal != null ? pctVal.toFixed(1) + "%" : "—"}
                                            </TableCell>
                                        </TableRow>
                                    );
                                })}

                                {/* Totals */}
                                <TableRow className="border-t-2 bg-sky-50 font-semibold">
                                    <TableCell colSpan={3} className="text-sky-800">Class A Total</TableCell>
                                    <TableCell className="text-right text-sky-800">{aud(classATotal)}</TableCell>
                                    <TableCell colSpan={2}/>
                                </TableRow>
                                <TableRow className="bg-teal-50 font-semibold">
                                    <TableCell colSpan={3} className="text-teal-800">Class B Total</TableCell>
                                    <TableCell className="text-right text-teal-800">{aud(classBTotal)}</TableCell>
                                    <TableCell colSpan={2}/>
                                </TableRow>
                                <TableRow className="bg-slate-100 font-bold text-base">
                                    <TableCell colSpan={3}>Grand Total</TableCell>
                                    <TableCell className="text-right">{aud(grandTotal)}</TableCell>
                                    <TableCell colSpan={2}/>
                                </TableRow>
                            </TableBody>
                        </Table>
                    )}
                </div>

                <DialogFooter className="flex-shrink-0 border-t pt-3">
                    <Button variant="outline" onClick={handleExport} disabled={!rows.length}>
                        <Download className="w-4 h-4 mr-2"/>
                        Export CSV
                    </Button>
                    <Button variant="outline" onClick={onClose}>Close</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
// ─── Sub-component: Allocation Row ───────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: AllocationRow
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function AllocationRow({row, canEdit, onSave, saving}) {
    const [editing, setEditing] = useState(false);
    const [allocType, setAllocType] = useState(row.allocation_type ?? "all_lots");
    const [classAPct, setClassAPct] = useState(row.class_a_pct ?? 50);
    const [classBPct, setClassBPct] = useState(row.class_b_pct ?? 50);

    const sumOk = Number(classAPct) + Number(classBPct) === 100;
    /**
     * @generated FunctionHeader
     * Function: handleSave
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSave = () => {
        if (allocType === "common" && !sumOk) {
            toast.error("Class A % and Class B % must sum to 100.");
            return;
        }
        onSave({
            ...row,
            allocation_type: allocType,
            class_a_pct: allocType === "common" ? Number(classAPct) : null,
            class_b_pct: allocType === "common" ? Number(classBPct) : null,
        });
        setEditing(false);
    };
    /**
     * @generated FunctionHeader
     * Function: handleCancel
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCancel = () => {
        setAllocType(row.allocation_type ?? "all_lots");
        setClassAPct(row.class_a_pct ?? 50);
        setClassBPct(row.class_b_pct ?? 50);
        setEditing(false);
    };

    return (
        <TableRow>
            <TableCell className="font-medium">{row.category_name}</TableCell>
            <TableCell><FundBadge fund={row.fund}/></TableCell>
            <TableCell>
                {editing && canEdit ? (
                    <Select value={allocType} onValueChange={(v) => setAllocType(v)}>
                        <SelectTrigger className="h-8 text-xs w-36">
                            <SelectValue/>
                        </SelectTrigger>
                        <SelectContent>
                            {ALLOC_TYPES.map((t) => (
                                <SelectItem key={t.value} value={t.value} className="text-xs">{t.label}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                ) : (
                    <AllocBadge type={row.allocation_type ?? "all_lots"}/>
                )}
            </TableCell>
            <TableCell>
                {editing && canEdit && allocType === "common" ? (
                    <div className="flex items-center gap-2">
                        <div className="flex items-center gap-1">
                            <span className="text-xs text-sky-700 font-medium w-14">Class A:</span>
                            <Input
                                type="number"
                                min={0}
                                max={100}
                                value={classAPct}
                                onChange={(e) => {
                                    setClassAPct(e.target.value);
                                    setClassBPct(100 - Number(e.target.value));
                                }}
                                className={`h-7 w-16 text-xs ${!sumOk ? "border-destructive" : ""}`}
                            />
                            <span className="text-xs">%</span>
                        </div>
                        <div className="flex items-center gap-1">
                            <span className="text-xs text-teal-700 font-medium w-14">Class B:</span>
                            <Input
                                type="number"
                                min={0}
                                max={100}
                                value={classBPct}
                                onChange={(e) => {
                                    setClassBPct(e.target.value);
                                    setClassAPct(100 - Number(e.target.value));
                                }}
                                className={`h-7 w-16 text-xs ${!sumOk ? "border-destructive" : ""}`}
                            />
                            <span className="text-xs">%</span>
                        </div>
                        {!sumOk && <span className="text-xs text-destructive">Must = 100%</span>}
                    </div>
                ) : row.allocation_type === "common" ? (
                    <span className="text-xs text-muted-foreground">
            A: {row.class_a_pct ?? "—"}% / B: {row.class_b_pct ?? "—"}%
          </span>
                ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                )}
            </TableCell>
            <TableCell className="text-right">{aud(row.budget_amount)}</TableCell>
            <TableCell>
                {canEdit && (
                    <div className="flex gap-1">
                        {editing ? (
                            <>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-7 w-7 text-emerald-600 hover:text-emerald-700"
                                    onClick={handleSave}
                                    disabled={saving}
                                >
                                    <Save className="w-3.5 h-3.5"/>
                                </Button>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-7 w-7 text-muted-foreground"
                                    onClick={handleCancel}
                                    disabled={saving}
                                >
                                    <X className="w-3.5 h-3.5"/>
                                </Button>
                            </>
                        ) : (
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7"
                                onClick={() => setEditing(true)}
                            >
                                <Edit2 className="w-3.5 h-3.5"/>
                            </Button>
                        )}
                    </div>
                )}
            </TableCell>
        </TableRow>
    );
}
// ─── Main Component ───────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: SchemeClassesPage
 * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function SchemeClassesPage() {
    const {user, api} = useAuth();

    const canEdit = MGMT_ROLES.includes(user?.role ?? "");
    const canDelete = DEL_ROLES.includes(user?.role ?? "");

    // ── Tab state ──────────────────────────────────────────────────────────────
    const [activeTab, setActiveTab] = useState("classes");

    // ── Scheme Classes state ───────────────────────────────────────────────────
    const [splitStatus, setSplitStatus] = useState(null);
    const [classA, setClassA] = useState(null);
    const [classB, setClassB] = useState(null);
    const [loadingClasses, setLoadingClasses] = useState(true);
    const [classesError, setClassesError] = useState(null);

    // Dialog state
    const [dialogOpen, setDialogOpen] = useState(false);
    const [dialogLabel, setDialogLabel] = useState("A");
    const [dialogExisting, setDialogExisting] = useState(null);
    const [savingClass, setSavingClass] = useState(false);

    // Delete confirmation
    const [deleteTarget, setDeleteTarget] = useState(null); // { cls, label }
    const [deleting, setDeleting] = useState(false);

    // Levy impact preview
    const [previewLoading, setPreviewLoading] = useState(false);
    const [previewData, setPreviewData] = useState(null);
    const [previewOpen, setPreviewOpen] = useState(false);
    const [previewFY, setPreviewFY] = useState(DEFAULT_FY);

    // ── Allocations state ──────────────────────────────────────────────────────
    const [allocFY, setAllocFY] = useState(DEFAULT_FY);
    const [allocations, setAllocations] = useState([]);
    const [loadingAllocs, setLoadingAllocs] = useState(false);
    const [allocsError, setAllocsError] = useState(null);
    const [savingAllocId, setSavingAllocId] = useState(null);
    const [loadingBudget, setLoadingBudget] = useState(false);

    // ── Fetch scheme classes ────────────────────────────────────────────────────
    const fetchClasses = useCallback(async () => {
        setLoadingClasses(true);
        setClassesError(null);
        try {
            const [statusRes, classesRes] = await Promise.all([
                api.get("/scheme-classes/status"),
                api.get("/scheme-classes"),
            ]);
            setSplitStatus(statusRes.data);
            const list = classesRes.data ?? [];
            setClassA(list.find((c) => c.class_label === "class_a") ?? null);
            setClassB(list.find((c) => c.class_label === "class_b") ?? null);
        } catch (err) {
            const msg = err?.response?.data?.detail ?? "Failed to load scheme classes.";
            setClassesError(msg);
            // If 404, the endpoint doesn't exist yet — treat as no data
            if (err?.response?.status !== 404) {
                toast.error(msg);
            }
        } finally {
            setLoadingClasses(false);
        }
    }, [api]);

    // ── Fetch allocations ───────────────────────────────────────────────────────
    const fetchAllocations = useCallback(async (fy) => {
        setLoadingAllocs(true);
        setAllocsError(null);
        try {
            const res = await api.get(`/scheme-classes/allocations/${fy}`);
            setAllocations(res.data ?? []);
        } catch (err) {
            if (err?.response?.status === 404) {
                setAllocations([]);
            } else {
                const msg = err?.response?.data?.detail ?? "Failed to load allocations.";
                setAllocsError(msg);
                toast.error(msg);
            }
        } finally {
            setLoadingAllocs(false);
        }
    }, [api]);

    useEffect(() => {
        fetchClasses();
    }, [fetchClasses]);
    useEffect(() => {
        fetchAllocations(allocFY);
    }, [fetchAllocations, allocFY]);
    // ── Open dialog helpers ─────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: openCreateA
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openCreateA = () => {
        setDialogLabel("A");
        setDialogExisting(null);
        setDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: openCreateB
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openCreateB = () => {
        setDialogLabel("B");
        setDialogExisting(null);
        setDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: openEditA
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openEditA = () => {
        setDialogLabel("A");
        setDialogExisting(classA);
        setDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: openEditB
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openEditB = () => {
        setDialogLabel("B");
        setDialogExisting(classB);
        setDialogOpen(true);
    };
    // ── Save class ──────────────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handleSaveClass
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSaveClass = async (payload) => {
        setSavingClass(true);
        try {
            const isEdit = !!dialogExisting?._id;
            if (isEdit) {
                await api.put(`/scheme-classes/${dialogExisting._id}`, payload);
                toast.success(`Class ${dialogLabel} updated successfully.`);
            } else {
                await api.post("/scheme-classes", payload);
                toast.success(`Class ${dialogLabel} created successfully.`);
            }
            setDialogOpen(false);
            fetchClasses();
        } catch (err) {
            toast.error(err?.response?.data?.detail ?? "Failed to save class.");
        } finally {
            setSavingClass(false);
        }
    };
    // ── Delete (deactivate) class ───────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async () => {
        if (!deleteTarget) return;
        setDeleting(true);
        try {
            await api.delete(`/scheme-classes/${deleteTarget.cls._id}`);
            toast.success(`Class ${deleteTarget.label} deactivated.`);
            setDeleteTarget(null);
            fetchClasses();
        } catch (err) {
            toast.error(err?.response?.data?.detail ?? "Failed to deactivate class.");
        } finally {
            setDeleting(false);
        }
    };
    // ── Preview levy impact ─────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handlePreview
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePreview = async () => {
        setPreviewLoading(true);
        try {
            const res = await api.post(`/scheme-classes/preview-levy-impact?financial_year=${previewFY}`
            );
            setPreviewData(res.data);
            setPreviewOpen(true);
        } catch (err) {
            toast.error(err?.response?.data?.detail ?? "Failed to generate levy impact preview.");
        } finally {
            setPreviewLoading(false);
        }
    };
    // ── Save allocation row ─────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handleSaveAlloc
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSaveAlloc = async (row) => {
        setSavingAllocId(row._id ?? row.category_name);
        try {
            if (row._id) {
                await api.put(`/scheme-classes/allocations/${row._id}`, row);
                toast.success("Allocation saved.");
            } else {
                // New allocation: budget_amount is dollars (display only), API needs amount_cents.
                // effective_from defaults to the start of the financial year (Jul 1 of first year).
                const startYear = ( allocFY || DEFAULT_FY ).split("-")[ 0 ];
                const payload = {
                    ...row,
                    financial_year: allocFY,
                    amount_cents: Math.round(( row.budget_amount ?? 0 ) * 100),
                    effective_from: `${startYear}-07-01T00:00:00Z`,
                };
                const res = await api.post("/scheme-classes/allocations", payload);
                toast.success("Allocation created.");
                // Replace the temporary row with the persisted one
                setAllocations((prev) =>
                    prev.map((a) => ( a.category_name === row.category_name ? res.data : a ))
                );
            }
            fetchAllocations(allocFY);
        } catch (err) {
            toast.error(err?.response?.data?.detail ?? "Failed to save allocation.");
        } finally {
            setSavingAllocId(null);
        }
    };
    // ── Load budget categories into allocations table ───────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handleLoadBudgetCategories
     * Path: frontend/src/pages/dashboard/admin/SchemeClassesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleLoadBudgetCategories = async () => {
        setLoadingBudget(true);
        try {
            // levy_categories uses `year` (e.g. "2026" from "2026-2027") and returns
            // fields: id, year, fund_type ("administrative"/"sinking"), name, budgeted_amount
            const yearKey = allocFY.split("-")[ 0 ];  // "2026-2027" → "2026"
            const res = await api.get(`/levy-categories?year=${yearKey}`);
            const cats = Array.isArray(res.data) ? res.data : [];
            if (!cats.length) {
                toast.info("No levy categories found for the selected financial year.");
                return;
            }
            // Merge: keep existing allocations, add missing categories as "all_lots"
            const existingNames = new Set(allocations.map((a) => a.category_name));
            const newRows = cats
                .filter((c) => !existingNames.has(c.name))
                .map((c) => ( {
                    category_id: c.id ?? c.name,
                    category_name: c.name ?? "Unknown",
                    // levy_categories fund_type: "administrative" → "admin", "sinking" → "sinking"
                    fund: c.fund_type?.toLowerCase().includes("sinking") ? "sinking" : "admin",
                    allocation_type: "all_lots",
                    class_a_pct: 50,
                    class_b_pct: 50,
                    budget_amount: c.budgeted_amount ?? 0,
                    _id: null,
                } ));
            setAllocations((prev) => [...prev, ...newRows]);
            toast.success(`Loaded ${newRows.length} levy categories.`);
        } catch (err) {
            toast.error(err?.response?.data?.detail ?? "Failed to load levy categories.");
        } finally {
            setLoadingBudget(false);
        }
    };

    // ── Render ─────────────────────────────────────────────────────────────────

    return (
        <TooltipProvider>
            <div className="space-y-6 p-6 max-w-6xl mx-auto">
                {/* Page Header */}
                <div className="flex items-start justify-between flex-wrap gap-3">
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
                            <Layers className="w-6 h-6 text-primary"/>
                            Scheme Class Management
                        </h1>
                        <p className="text-muted-foreground text-sm mt-0.5">
                            Define Class A / Class B splits and allocate budget categories to each class.
                        </p>
                    </div>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                            fetchClasses();
                            fetchAllocations(allocFY);
                        }}
                        disabled={loadingClasses || loadingAllocs}
                    >
                        <RefreshCw
                            className={`w-4 h-4 mr-1.5 ${( loadingClasses || loadingAllocs ) ? "animate-spin" : ""}`}/>
                        Refresh
                    </Button>
                </div>

                {/* Legal info banner */}
                <LegalInfoBanner/>

                {/* Tabs */}
                <Tabs value={activeTab} onValueChange={setActiveTab}>
                    <TabsList className="grid grid-cols-2 w-full max-w-sm">
                        <TabsTrigger value="classes">
                            <Building2 className="w-4 h-4 mr-1.5"/>
                            Scheme Classes
                        </TabsTrigger>
                        <TabsTrigger value="allocations">
                            <DollarSign className="w-4 h-4 mr-1.5"/>
                            Category Allocations
                        </TabsTrigger>
                    </TabsList>

                    {/* ── Tab 1: Scheme Classes ───────────────────────────────────────── */}
                    <TabsContent value="classes" className="space-y-5 mt-4">

                        {/* Split Status */}
                        {loadingClasses ? (
                            <Card>
                                <CardContent className="py-8 text-center text-muted-foreground">
                                    <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2"/>
                                    Loading scheme class data…
                                </CardContent>
                            </Card>
                        ) : classesError && !classA && !classB ? (
                            <Card className="border-destructive/40 bg-destructive/5">
                                <CardContent className="py-8 text-center text-destructive">
                                    <AlertCircle className="w-6 h-6 mx-auto mb-2"/>
                                    <p className="font-medium">Failed to load scheme classes</p>
                                    <p className="text-sm text-muted-foreground mt-1">{classesError}</p>
                                    <Button variant="outline" size="sm" className="mt-3" onClick={fetchClasses}>
                                        <RefreshCw className="w-4 h-4 mr-1.5"/>
                                        Try Again
                                    </Button>
                                </CardContent>
                            </Card>
                        ) : (
                            <>
                                <SplitStatusCard
                                    status={splitStatus}
                                    onPreview={handlePreview}
                                    previewLoading={previewLoading}
                                />

                                {/* Preview FY selector (inline below status) */}
                                <div className="flex items-center gap-2 text-sm">
                                    <span className="text-muted-foreground">Preview for financial year:</span>
                                    <Select value={previewFY} onValueChange={setPreviewFY}>
                                        <SelectTrigger className="h-8 w-32 text-xs">
                                            <SelectValue/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            {FINANCIAL_YEARS.map((fy) => (
                                                <SelectItem key={fy} value={fy} className="text-xs">{fy}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>

                                {/* Two-column class cards */}
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <ClassCard
                                        cls={classA}
                                        classLabel="A"
                                        canEdit={canEdit}
                                        canDelete={canDelete}
                                        loading={loadingClasses}
                                        onEdit={classA ? openEditA : openCreateA}
                                        onDelete={() => setDeleteTarget({cls: classA, label: "A"})}
                                    />
                                    <ClassCard
                                        cls={classB}
                                        classLabel="B"
                                        canEdit={canEdit}
                                        canDelete={canDelete}
                                        loading={loadingClasses}
                                        onEdit={classB ? openEditB : openCreateB}
                                        onDelete={() => setDeleteTarget({cls: classB, label: "B"})}
                                    />
                                </div>

                                {/* Warning if units overlap */}
                                {classA && classB && ( () => {
                                    const setA = new Set(classA.unit_numbers ?? []);
                                    const overlap = ( classB.unit_numbers ?? [] ).filter((u) => setA.has(u));
                                    if (!overlap.length) return null;
                                    return (
                                        <Card className="border-red-300 bg-red-50">
                                            <CardContent className="py-3 flex items-start gap-2 text-sm text-red-800">
                                                <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5"/>
                                                <div>
                                                    <strong>Unit overlap detected:</strong> The following units appear
                                                    in both classes:{" "}
                                                    {overlap.map((u) => (
                                                        <Badge key={u} variant="outline"
                                                               className="text-red-700 border-red-300 mr-1 font-mono text-xs">{u}</Badge>
                                                    ))}
                                                    <span className="block mt-1 text-xs text-red-700">Each unit must belong to exactly one class.</span>
                                                </div>
                                            </CardContent>
                                        </Card>
                                    );
                                } )()}
                            </>
                        )}
                    </TabsContent>

                    {/* ── Tab 2: Category Allocations ─────────────────────────────────── */}
                    <TabsContent value="allocations" className="space-y-5 mt-4">

                        {/* Controls */}
                        <div className="flex items-center justify-between flex-wrap gap-3">
                            <div className="flex items-center gap-2">
                                <Label className="text-sm font-medium">Financial Year:</Label>
                                <Select value={allocFY} onValueChange={setAllocFY}>
                                    <SelectTrigger className="h-8 w-32 text-xs">
                                        <SelectValue/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        {FINANCIAL_YEARS.map((fy) => (
                                            <SelectItem key={fy} value={fy} className="text-xs">{fy}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>

                            {canEdit && (
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={handleLoadBudgetCategories}
                                    disabled={loadingBudget || loadingAllocs}
                                >
                                    {loadingBudget ? (
                                        <><RefreshCw className="w-4 h-4 mr-2 animate-spin"/>Loading…</>
                                    ) : (
                                        <><FileText className="w-4 h-4 mr-2"/>Load Budget Categories</>
                                    )}
                                </Button>
                            )}
                        </div>

                        {/* Info notice */}
                        <Card className="border-sky-200 bg-sky-50">
                            <CardContent className="py-3 flex items-start gap-2 text-xs text-sky-900">
                                <Info className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-sky-600"/>
                                <span>
                  Categories default to <strong>All Lots</strong> (single pool, no class split) until
                  explicitly allocated here. Use <strong>Common (Split)</strong> for shared facilities
                  such as common area maintenance — then specify the cost-sharing ratio between classes.
                </span>
                            </CardContent>
                        </Card>

                        {/* Table */}
                        {loadingAllocs ? (
                            <Card>
                                <CardContent className="py-10 text-center text-muted-foreground">
                                    <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2"/>
                                    Loading allocations…
                                </CardContent>
                            </Card>
                        ) : allocsError ? (
                            <Card className="border-destructive/40 bg-destructive/5">
                                <CardContent className="py-8 text-center text-destructive">
                                    <AlertCircle className="w-6 h-6 mx-auto mb-2"/>
                                    <p className="font-medium">Failed to load allocations</p>
                                    <p className="text-sm text-muted-foreground mt-1">{allocsError}</p>
                                    <Button variant="outline" size="sm" className="mt-3"
                                            onClick={() => fetchAllocations(allocFY)}>
                                        <RefreshCw className="w-4 h-4 mr-1.5"/>
                                        Try Again
                                    </Button>
                                </CardContent>
                            </Card>
                        ) : allocations.length === 0 ? (
                            <Card className="border-dashed">
                                <CardContent className="py-12 text-center text-muted-foreground">
                                    <Layers className="w-10 h-10 mx-auto mb-3 opacity-25"/>
                                    <p className="font-medium">No category allocations defined</p>
                                    <p className="text-sm mt-1 max-w-sm mx-auto">
                                        Categories default to <strong>All Lots</strong> (single pool) until allocated.
                                        Click <strong>Load Budget Categories</strong> to pre-fill from
                                        the {allocFY} budget.
                                    </p>
                                    {canEdit && (
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="mt-4"
                                            onClick={handleLoadBudgetCategories}
                                            disabled={loadingBudget}
                                        >
                                            <FileText className="w-4 h-4 mr-2"/>
                                            Load Budget Categories
                                        </Button>
                                    )}
                                </CardContent>
                            </Card>
                        ) : (
                            <Card>
                                <CardHeader className="pb-3">
                                    <div className="flex items-center justify-between">
                                        <CardTitle className="text-base">
                                            Budget Category Allocations — {allocFY}
                                        </CardTitle>
                                        <Badge variant="outline" className="text-xs">
                                            {allocations.length} categories
                                        </Badge>
                                    </div>
                                    <CardDescription className="text-xs">
                                        Edit each row inline. Class A Only / Class B Only rows are charged solely to
                                        that class.
                                        Common rows split by the stated percentages.
                                    </CardDescription>
                                </CardHeader>
                                <CardContent className="p-0">
                                    <div className="overflow-x-auto">
                                        <Table>
                                            <TableHeader>
                                                <TableRow>
                                                    <TableHead>Category</TableHead>
                                                    <TableHead>Fund</TableHead>
                                                    <TableHead>Allocation Type</TableHead>
                                                    <TableHead>Split Ratio</TableHead>
                                                    <TableHead className="text-right">Budget</TableHead>
                                                    <TableHead className="w-20">Actions</TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {allocations.map((row, idx) => (
                                                    <AllocationRow
                                                        key={row._id ?? row.category_name ?? idx}
                                                        row={row}
                                                        canEdit={canEdit}
                                                        saving={savingAllocId === ( row._id ?? row.category_name )}
                                                        onSave={handleSaveAlloc}
                                                    />
                                                ))}
                                            </TableBody>
                                        </Table>
                                    </div>
                                </CardContent>
                            </Card>
                        )}
                    </TabsContent>
                </Tabs>
            </div>

            {/* ── Create/Edit Class Dialog ──────────────────────────────────────── */}
            <ClassDialog
                open={dialogOpen}
                onClose={() => setDialogOpen(false)}
                classLabel={dialogLabel}
                existing={dialogExisting}
                onSave={handleSaveClass}
                saving={savingClass}
            />

            {/* ── Delete Confirmation Dialog ────────────────────────────────────── */}
            <Dialog open={!!deleteTarget} onOpenChange={(v) => !v && setDeleteTarget(null)}>
                <DialogContent className="max-w-sm">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2 text-destructive">
                            <Trash2 className="w-5 h-5"/>
                            Deactivate Class {deleteTarget?.label}?
                        </DialogTitle>
                        <DialogDescription>
                            This will mark{" "}
                            <strong>{deleteTarget?.cls?.class_name}</strong> as inactive. The
                            class definition is retained for audit purposes. This does NOT affect
                            any legal registration with the ACT Land Titles Office.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDeleteTarget(null)} disabled={deleting}>
                            Cancel
                        </Button>
                        <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
                            {deleting ? (
                                <><RefreshCw className="w-4 h-4 mr-2 animate-spin"/>Deactivating…</>
                            ) : (
                                <><Trash2 className="w-4 h-4 mr-2"/>Deactivate</>
                            )}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* ── Levy Impact Preview Dialog ────────────────────────────────────── */}
            <LevyImpactDialog
                open={previewOpen}
                onClose={() => setPreviewOpen(false)}
                data={previewData}
                financialYear={previewFY}
            />
        </TooltipProvider>
    );
}
