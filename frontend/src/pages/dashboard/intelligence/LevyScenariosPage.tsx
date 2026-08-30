// @ts-nocheck
"use client";

/**
 * LevyScenariosPage
 * -----------------
 * Model what-if levy allocation scenarios for a building.
 *
 * Tab 1 — Scenarios:  list/create/clone/delete named scenarios
 * Tab 2 — Editor:     configure groups, budgets, UOE overrides
 * Tab 3 — Results:    per-unit levy computation and comparison table
 */

import {ReactElement, useCallback, useEffect, useMemo, useState} from "react";
import {useSession} from "next-auth/react";
import axios from "axios";
import {Tabs, TabsContent, TabsList, TabsTrigger} from "@/components/ui/tabs";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Badge} from "@/components/ui/badge";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Label} from "@/components/ui/label";
import {Textarea} from "@/components/ui/textarea";
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue,} from "@/components/ui/select";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow,} from "@/components/ui/table";
import {Accordion, AccordionContent, AccordionItem, AccordionTrigger,} from "@/components/ui/accordion";
import {PageHeader} from "@/components/shared/PageHeader";
import {
    AlertCircle,
    AlertTriangle,
    CheckCircle2,
    Copy,
    Download,
    Minus,
    Play,
    Plus,
    RefreshCw,
    ShieldAlert,
    SlidersHorizontal,
    Trash2,
    TrendingDown,
    TrendingUp,
    Users,
} from "lucide-react";
import {toast} from "sonner";

import {formatMoneyFromDollars} from '@/lib/currency';
const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8003";
const API = `${BACKEND_URL}/api/levy-scenarios`;

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface UnitInfo {
    unit_number: string;
    entitlement: number;
    unit_type?: string;
    scheme_class?: string | null;
}

interface BaseData {
    building_id: string;
    financial_year: string;
    total_admin_budget: number;
    total_sinking_budget: number;
    current_admin_rate_per_uoe: number;
    current_sinking_rate_per_uoe: number;
    total_uoe: number;
    unit_count: number;
    units: UnitInfo[];
    available_years: string[];
}

interface ScenarioGroup {
    group_name: string;
    color: string;
    unit_numbers: string[];
    unit_type_prefixes: string[];
    admin_share_pct: number;
    sinking_share_pct: number;
    uoe_overrides: { unit_number: string; adjusted_uoe: number }[];
}

interface Scenario {
    id: string;
    scenario_name: string;
    description?: string;
    financial_year: string;
    total_admin_budget: number;
    total_sinking_budget: number;
    groups: ScenarioGroup[];
    status: "draft" | "computed" | "published";
    last_result?: any;
    created_by: string;
    created_at: string;
    updated_at: string;
    published_at?: string;
}

interface UnitResult {
    unit_number: string;
    group_name: string;
    group_color: string;
    original_uoe: number;
    scenario_uoe: number;
    uoe_changed: boolean;
    current_total_levy: number;
    scenario_total_levy: number;
    total_delta: number;
    total_delta_pct: number;
    scenario_admin_levy: number;
    scenario_sinking_levy: number;
    current_admin_levy: number;
    current_sinking_levy: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const GROUP_COLORS = [
    "#6366f1", "#f59e0b", "#10b981", "#ef4444",
    "#3b82f6", "#8b5cf6", "#ec4899", "#14b8a6",
];

const MANAGE_ROLES = ["ec_member", "strata_manager", "super_admin"];
const APPLY_UOE_ROLES = ["strata_manager", "super_admin"];

const STATUS_BADGES: Record<string, ReactElement> = {
    draft: <Badge variant="outline" className="text-muted-foreground">Draft</Badge>,
    computed: <Badge className="bg-primary/10 text-primary">Computed</Badge>,
    published: <Badge className="bg-emerald-100 text-emerald-700"><CheckCircle2
        className="h-3 w-3 mr-1"/>Published</Badge>,
};
// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: fmtPct
 * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmtPct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
/**
 * @generated FunctionHeader
 * Function: DeltaBadge
 * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function DeltaBadge({delta, pct}: { delta: number; pct: number }) {
    if (Math.abs(delta) < 0.01) return <span className="text-muted-foreground text-xs">No change</span>;
    const pos = delta > 0;
    return (
        <span className={`flex items-center gap-1 text-xs font-medium ${pos ? "text-red-600" : "text-emerald-600"}`}>
      {pos ? <TrendingUp className="h-3 w-3"/> : <TrendingDown className="h-3 w-3"/>}
            {formatMoneyFromDollars(Math.abs(delta))} ({fmtPct(pct)})
    </span>
    );
}
/**
 * @generated FunctionHeader
 * Function: ShareBar
 * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ShareBar({groups}: { groups: ScenarioGroup[] }) {
    return (
        <div className="flex h-3 w-full rounded-full overflow-hidden mt-2">
            {groups.map((g, i) => (
                <div
                    key={i}
                    style={{
                        width: `${g.admin_share_pct}%`,
                        backgroundColor: g.color || GROUP_COLORS[i % GROUP_COLORS.length]
                    }}
                    title={`${g.group_name}: ${g.admin_share_pct}%`}
                />
            ))}
        </div>
    );
}
// ─────────────────────────────────────────────────────────────────────────────
// Empty group factory
// ─────────────────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: newGroup
 * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function newGroup(idx: number, totalGroups: number): ScenarioGroup {
    const equalShare = totalGroups > 0 ? parseFloat((100 / totalGroups).toFixed(2)) : 50;
    return {
        group_name: `Group ${idx + 1}`,
        color: GROUP_COLORS[idx % GROUP_COLORS.length],
        unit_numbers: [],
        unit_type_prefixes: [],
        admin_share_pct: equalShare,
        sinking_share_pct: equalShare,
        uoe_overrides: [],
    };
}
// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: LevyScenariosPage
 * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function LevyScenariosPage() {
    const sessionResult = useSession();
    const session = sessionResult?.data as any;
    const token = session?.accessToken;
    const userRole = session?.user?.data?.role ?? "";

    const canManage = MANAGE_ROLES.includes(userRole);
    const canApplyUoe = APPLY_UOE_ROLES.includes(userRole);

    const headers = useCallback(
        () => ({Authorization: `Bearer ${token}`}),
        [token]
    );

    // ── Global state ──────────────────────────────────────────────────────────
    const [activeTab, setActiveTab] = useState("scenarios");
    const [scenarios, setScenarios] = useState<Scenario[]>([]);
    const [baseData, setBaseData] = useState<BaseData | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [selectedFY, setSelectedFY] = useState<string>("");

    // ── Selected scenario for editing / results ────────────────────────────
    const [editingId, setEditingId] = useState<string | null>(null);
    const editingScenario = useMemo(
        () => scenarios.find(s => s.id === editingId) ?? null,
        [scenarios, editingId]
    );

    // ── Create dialog ─────────────────────────────────────────────────────────
    const [createOpen, setCreateOpen] = useState(false);
    const [createForm, setCreateForm] = useState({
        scenario_name: "",
        description: "",
        financial_year: "",
        total_admin_budget: 0,
        total_sinking_budget: 0,
        groups: [newGroup(0, 2), newGroup(1, 2)] as ScenarioGroup[],
    });
    const [creating, setCreating] = useState(false);

    // ── Editor state (for the selected scenario) ──────────────────────────────
    const [editorGroups, setEditorGroups] = useState<ScenarioGroup[]>([]);
    const [editorBudgets, setEditorBudgets] = useState({admin: 0, sinking: 0});
    const [isSaving, setIsSaving] = useState(false);
    const [isComputing, setIsComputing] = useState(false);

    // ── Results state ─────────────────────────────────────────────────────────
    const [resultFilter, setResultFilter] = useState<string>("all");
    const [resultSort, setResultSort] = useState<"unit" | "delta" | "group">("unit");
    const [applyingUoe, setApplyingUoe] = useState(false);
    const [applyConfirmOpen, setApplyConfirmOpen] = useState(false);

    // ─────────────────────────────────────────────────────────────────────────
    // Data loading
    // ─────────────────────────────────────────────────────────────────────────

    const loadScenarios = useCallback(async () => {
        if (!session) return;
        setIsLoading(true);
        try {
            const res = await axios.get(API, {headers: headers()});
            setScenarios(res.data.data?.items ?? []);
        } catch {
            toast.error("Failed to load scenarios.");
        } finally {
            setIsLoading(false);
        }
    }, [session, headers]);

    const loadBaseData = useCallback(async (fy?: string) => {
        if (!session) return;
        try {
            const res = await axios.get(`${API}/base-data${fy ? `?financial_year=${fy}` : ""}`, {
                headers: headers(),
            });
            const data: BaseData = res.data.data;
            setBaseData(data);
            if (!selectedFY) setSelectedFY(data.financial_year);
            setCreateForm(f => ({
                ...f,
                financial_year: data.financial_year,
                total_admin_budget: data.total_admin_budget,
                total_sinking_budget: data.total_sinking_budget,
            }));
        } catch {
            // Non-fatal — base data is convenience only
        }
    }, [session, headers, selectedFY]);

    useEffect(() => {
        loadScenarios();
    }, [loadScenarios]);
    useEffect(() => {
        loadBaseData();
    }, [loadBaseData]);

    // Sync editor when scenario selection changes
    useEffect(() => {
        if (editingScenario) {
            setEditorGroups(editingScenario.groups.map(g => ({...g, uoe_overrides: g.uoe_overrides || []})));
            setEditorBudgets({
                admin: editingScenario.total_admin_budget,
                sinking: editingScenario.total_sinking_budget,
            });
        }
    }, [editingScenario]);

    // ─────────────────────────────────────────────────────────────────────────
    // Validations
    // ─────────────────────────────────────────────────────────────────────────

    const adminShareTotal = editorGroups.reduce((s, g) => s + (g.admin_share_pct || 0), 0);
    const sinkingShareTotal = editorGroups.reduce((s, g) => s + (g.sinking_share_pct || 0), 0);
    const sharesValid = Math.abs(adminShareTotal - 100) < 0.1 && Math.abs(sinkingShareTotal - 100) < 0.1;

    const createAdminTotal = createForm.groups.reduce((s, g) => s + (g.admin_share_pct || 0), 0);
    const createSinkingTotal = createForm.groups.reduce((s, g) => s + (g.sinking_share_pct || 0), 0);
    const createSharesValid = Math.abs(createAdminTotal - 100) < 0.1 && Math.abs(createSinkingTotal - 100) < 0.1;
    // ─────────────────────────────────────────────────────────────────────────
    // CRUD handlers
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @generated FunctionHeader
     * Function: handleCreate
     * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCreate = async () => {
        if (!createSharesValid) {
            toast.error("Group budget shares must sum to 100% for both admin and sinking funds.");
            return;
        }
        setCreating(true);
        try {
            await axios.post(API, createForm, {headers: headers()});
            toast.success("Scenario created.");
            setCreateOpen(false);
            await loadScenarios();
        } catch (e: any) {
            toast.error(e.response?.data?.detail || "Failed to create scenario.");
        } finally {
            setCreating(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async (id: string, name: string) => {
        if (!confirm(`Delete scenario "${name}"? This cannot be undone.`)) return;
        try {
            await axios.delete(`${API}/${id}`, {headers: headers()});
            toast.success("Scenario deleted.");
            if (editingId === id) setEditingId(null);
            await loadScenarios();
        } catch (e: any) {
            toast.error(e.response?.data?.detail || "Failed to delete.");
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleClone
     * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleClone = async (id: string) => {
        try {
            await axios.post(`${API}/${id}/clone`, {}, {headers: headers()});
            toast.success("Scenario cloned.");
            await loadScenarios();
        } catch (e: any) {
            toast.error(e.response?.data?.detail || "Failed to clone.");
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleSaveEditor
     * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSaveEditor = async () => {
        if (!editingId) return;
        if (!sharesValid) {
            toast.error("Shares must sum to 100% before saving.");
            return;
        }
        setIsSaving(true);
        try {
            await axios.put(`${API}/${editingId}`, {
                total_admin_budget: editorBudgets.admin,
                total_sinking_budget: editorBudgets.sinking,
                groups: editorGroups,
            }, {headers: headers()});
            toast.success("Scenario saved.");
            await loadScenarios();
        } catch (e: any) {
            toast.error(e.response?.data?.detail || "Failed to save.");
        } finally {
            setIsSaving(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleCompute
     * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCompute = async () => {
        if (!editingId) return;
        // Auto-save first if there are unsaved changes
        if (!sharesValid) {
            toast.error("Fix share totals before computing.");
            return;
        }
        setIsComputing(true);
        try {
            // Save current editor state first
            await axios.put(`${API}/${editingId}`, {
                total_admin_budget: editorBudgets.admin,
                total_sinking_budget: editorBudgets.sinking,
                groups: editorGroups,
            }, {headers: headers()});

            await axios.post(`${API}/${editingId}/compute`, {}, {headers: headers()});
            toast.success("Computation complete.");
            await loadScenarios();
            setActiveTab("results");
        } catch (e: any) {
            toast.error(e.response?.data?.detail || "Computation failed.");
        } finally {
            setIsComputing(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleApplyUoe
     * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleApplyUoe = async () => {
        if (!editingId) return;
        setApplyingUoe(true);
        try {
            const res = await axios.post(`${API}/${editingId}/apply-uoe`, {}, {headers: headers()});
            toast.success(res.data.data.message);
            setApplyConfirmOpen(false);
            await loadScenarios();
        } catch (e: any) {
            toast.error(e.response?.data?.detail || "Failed to apply UOE changes.");
        } finally {
            setApplyingUoe(false);
        }
    };
    // ─────────────────────────────────────────────────────────────────────────
    // Group editor helpers
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @generated FunctionHeader
     * Function: addGroup
     * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const addGroup = () => {
        setEditorGroups(gs => [...gs, newGroup(gs.length, gs.length + 1)]);
    };
    /**
     * @generated FunctionHeader
     * Function: removeGroup
     * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const removeGroup = (idx: number) => {
        setEditorGroups(gs => gs.filter((_, i) => i !== idx));
    };
    /**
     * @generated FunctionHeader
     * Function: updateGroup
     * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateGroup = (idx: number, patch: Partial<ScenarioGroup>) => {
        setEditorGroups(gs => gs.map((g, i) => i === idx ? {...g, ...patch} : g));
    };
    /**
     * @generated FunctionHeader
     * Function: distributeEvenly
     * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const distributeEvenly = () => {
        const n = editorGroups.length || 1;
        const share = parseFloat((100 / n).toFixed(2));
        const remainder = parseFloat((100 - share * (n - 1)).toFixed(2));
        setEditorGroups(gs => gs.map((g, i) => ({
            ...g,
            admin_share_pct: i === n - 1 ? remainder : share,
            sinking_share_pct: i === n - 1 ? remainder : share,
        })));
    };

    // ─────────────────────────────────────────────────────────────────────────
    // Results helpers
    // ─────────────────────────────────────────────────────────────────────────

    const unitResults: UnitResult[] = useMemo(() => {
        const raw = editingScenario?.last_result?.unit_results ?? [];
        let filtered = resultFilter === "all" ? raw
            : resultFilter === "rising" ? raw.filter((u: UnitResult) => u.total_delta > 0.01)
                : resultFilter === "falling" ? raw.filter((u: UnitResult) => u.total_delta < -0.01)
                    : raw.filter((u: UnitResult) => u.group_name === resultFilter);
        if (resultSort === "delta") {
            filtered = [...filtered].sort((a: UnitResult, b: UnitResult) => b.total_delta - a.total_delta);
        } else if (resultSort === "group") {
            filtered = [...filtered].sort((a: UnitResult, b: UnitResult) => a.group_name.localeCompare(b.group_name));
        } else {
            filtered = [...filtered].sort((a: UnitResult, b: UnitResult) => a.unit_number.localeCompare(b.unit_number));
        }
        return filtered;
    }, [editingScenario, resultFilter, resultSort]);

    const groupNames = useMemo(
        () => [...new Set((editingScenario?.last_result?.unit_results ?? []).map((u: UnitResult) => u.group_name))],
        [editingScenario]
    );
    /**
     * @generated FunctionHeader
     * Function: exportCSV
     * Path: frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const exportCSV = () => {
        const rows = [
            ["Unit", "Group", "Original UOE", "Scenario UOE", "Current Total Levy", "Scenario Total Levy", "Delta", "Delta %"],
            ...unitResults.map(u => [
                u.unit_number, u.group_name,
                u.original_uoe.toFixed(4), u.scenario_uoe.toFixed(4),
                u.current_total_levy.toFixed(2), u.scenario_total_levy.toFixed(2),
                u.total_delta.toFixed(2), u.total_delta_pct.toFixed(2),
            ]),
        ];
        const csv = rows.map(r => r.join(",")).join("\n");
        const blob = new Blob([csv], {type: "text/csv"});
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `levy-scenario-${editingScenario?.scenario_name ?? "results"}.csv`;
        a.click();
    };

    // ─────────────────────────────────────────────────────────────────────────
    // Render
    // ─────────────────────────────────────────────────────────────────────────

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <PageHeader
                    className="flex-1 border-b-0 pb-0"
                    title="Levy Scenario Modeller"
                    icon={<SlidersHorizontal className="h-5 w-5"/>}
                    description="Model what-if levy allocations — divide units into groups, set budget shares, and compare per-unit levies before any formal resolution."
                />
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={loadScenarios}>
                        <RefreshCw className="h-4 w-4 mr-1"/> Refresh
                    </Button>
                    {canManage && (
                        <Button size="sm" onClick={() => setCreateOpen(true)}>
                            <Plus className="h-4 w-4 mr-1"/> New Scenario
                        </Button>
                    )}
                </div>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList>
                    <TabsTrigger value="scenarios">Scenarios</TabsTrigger>
                    <TabsTrigger value="editor" disabled={!editingId}>Editor</TabsTrigger>
                    <TabsTrigger value="results" disabled={!editingScenario?.last_result}>
                        Results
                        {editingScenario?.last_result && (
                            <Badge className="ml-2 bg-primary/10 text-primary text-xs">
                                {editingScenario.last_result.unit_results?.length ?? 0}
                            </Badge>
                        )}
                    </TabsTrigger>
                </TabsList>

                {/* ── Tab 1: Scenarios List ────────────────────────────────────── */}
                <TabsContent value="scenarios" className="space-y-4">
                    {isLoading ? (
                        <div className="flex justify-center py-12">
                            <RefreshCw className="animate-spin h-6 w-6 text-muted-foreground"/>
                        </div>
                    ) : scenarios.length === 0 ? (
                        <Card>
                            <CardContent className="pt-8 pb-8 text-center text-muted-foreground space-y-3">
                                <SlidersHorizontal className="h-10 w-10 mx-auto opacity-30"/>
                                <div>No scenarios yet.</div>
                                {canManage && (
                                    <Button size="sm" onClick={() => setCreateOpen(true)}>
                                        <Plus className="h-4 w-4 mr-1"/> Create your first scenario
                                    </Button>
                                )}
                            </CardContent>
                        </Card>
                    ) : (
                        <div className="space-y-3">
                            {scenarios.map(s => (
                                <Card
                                    key={s.id}
                                    className={`cursor-pointer transition-all ${editingId === s.id ? "ring-2 ring-primary" : "hover:shadow-md"}`}
                                    onClick={() => {
                                        setEditingId(s.id);
                                        setActiveTab("editor");
                                    }}
                                >
                                    <CardContent className="pt-4 pb-4">
                                        <div className="flex items-start justify-between gap-4">
                                            <div className="flex-1 min-w-0">
                                                <div className="flex items-center gap-2">
                                                    <span className="font-semibold truncate">{s.scenario_name}</span>
                                                    {STATUS_BADGES[s.status]}
                                                    <Badge variant="outline"
                                                           className="text-xs">{s.financial_year}</Badge>
                                                </div>
                                                {s.description && (
                                                    <p className="text-xs text-muted-foreground mt-1 truncate">{s.description}</p>
                                                )}
                                                <div className="flex gap-4 mt-2 text-xs text-muted-foreground">
                          <span className="flex items-center gap-1">
                            <Users className="h-3 w-3"/> {s.groups.length} groups
                          </span>
                                                    <span>Admin: {formatMoneyFromDollars(s.total_admin_budget)}</span>
                                                    <span>Sinking: {formatMoneyFromDollars(s.total_sinking_budget)}</span>
                                                </div>
                                                {s.groups.length > 0 && <ShareBar groups={s.groups}/>}
                                            </div>
                                            <div className="flex items-center gap-2 shrink-0"
                                                 onClick={e => e.stopPropagation()}>
                                                {canManage && (
                                                    <>
                                                        <Button variant="ghost" size="sm" title="Clone"
                                                                onClick={() => handleClone(s.id)}>
                                                            <Copy className="h-4 w-4"/>
                                                        </Button>
                                                        <Button variant="ghost" size="sm" title="Delete"
                                                                className="text-red-500 hover:text-red-700"
                                                                onClick={() => handleDelete(s.id, s.scenario_name)}>
                                                            <Trash2 className="h-4 w-4"/>
                                                        </Button>
                                                    </>
                                                )}
                                            </div>
                                        </div>
                                    </CardContent>
                                </Card>
                            ))}
                        </div>
                    )}
                </TabsContent>

                {/* ── Tab 2: Editor ─────────────────────────────────────────────── */}
                <TabsContent value="editor" className="space-y-4">
                    {!editingScenario ? (
                        <Card>
                            <CardContent className="pt-6 text-center text-muted-foreground">
                                Select a scenario from the Scenarios tab to edit it.
                            </CardContent>
                        </Card>
                    ) : (
                        <>
                            {/* Budget inputs */}
                            <Card>
                                <CardHeader className="pb-2">
                                    <CardTitle className="text-base">Budget</CardTitle>
                                    <CardDescription>{editingScenario.financial_year}</CardDescription>
                                </CardHeader>
                                <CardContent>
                                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                        <div>
                                            <Label>Total Admin Fund Budget (AUD)</Label>
                                            <Input
                                                type="number"
                                                step="0.01"
                                                value={editorBudgets.admin}
                                                onChange={e => setEditorBudgets(b => ({
                                                    ...b,
                                                    admin: parseFloat(e.target.value) || 0
                                                }))}
                                            />
                                            {baseData && (
                                                <p className="text-xs text-muted-foreground mt-1">
                                                    Current: {formatMoneyFromDollars(baseData.total_admin_budget)}
                                                </p>
                                            )}
                                        </div>
                                        <div>
                                            <Label>Total Sinking Fund Budget (AUD)</Label>
                                            <Input
                                                type="number"
                                                step="0.01"
                                                value={editorBudgets.sinking}
                                                onChange={e => setEditorBudgets(b => ({
                                                    ...b,
                                                    sinking: parseFloat(e.target.value) || 0
                                                }))}
                                            />
                                            {baseData && (
                                                <p className="text-xs text-muted-foreground mt-1">
                                                    Current: {formatMoneyFromDollars(baseData.total_sinking_budget)}
                                                </p>
                                            )}
                                        </div>
                                    </div>
                                </CardContent>
                            </Card>

                            {/* Groups */}
                            <Card>
                                <CardHeader className="pb-2">
                                    <div className="flex items-center justify-between">
                                        <div>
                                            <CardTitle className="text-base">Unit Groups</CardTitle>
                                            <CardDescription>
                                                Divide units into groups and assign each group a percentage of the
                                                budget.
                                                Admin + Sinking shares must each sum to 100%.
                                            </CardDescription>
                                        </div>
                                        <div className="flex gap-2">
                                            <Button variant="outline" size="sm" onClick={distributeEvenly}>
                                                <Minus className="h-3 w-3 mr-1"/> Distribute Evenly
                                            </Button>
                                            {canManage && (
                                                <Button variant="outline" size="sm" onClick={addGroup}>
                                                    <Plus className="h-3 w-3 mr-1"/> Add Group
                                                </Button>
                                            )}
                                        </div>
                                    </div>
                                </CardHeader>
                                <CardContent className="space-y-3">
                                    {/* Share totals */}
                                    <div
                                        className={`flex gap-4 text-xs p-2 rounded-md ${sharesValid ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"}`}>
                                        {sharesValid
                                            ? <><CheckCircle2 className="h-4 w-4"/> Admin
                                                shares: {adminShareTotal.toFixed(2)}% · Sinking
                                                shares: {sinkingShareTotal.toFixed(2)}% — ✓ valid</>
                                            : <><AlertCircle className="h-4 w-4"/> Admin: {adminShareTotal.toFixed(2)}%
                                                · Sinking: {sinkingShareTotal.toFixed(2)}% — must both equal 100%</>
                                        }
                                    </div>

                                    {editorGroups.length > 0 && <ShareBar groups={editorGroups}/>}

                                    <Accordion type="multiple" className="space-y-2">
                                        {editorGroups.map((grp, idx) => (
                                            <AccordionItem key={idx} value={`grp-${idx}`}
                                                           className="border rounded-lg overflow-hidden">
                                                <AccordionTrigger className="px-4 py-3 hover:no-underline">
                                                    <div className="flex items-center gap-3 w-full text-left">
                                                        <div className="w-4 h-4 rounded-full shrink-0"
                                                             style={{backgroundColor: grp.color}}/>
                                                        <span
                                                            className="font-medium text-sm">{grp.group_name || `Group ${idx + 1}`}</span>
                                                        <div
                                                            className="flex gap-2 text-xs text-muted-foreground ml-auto mr-4">
                                                            <span>Admin: {grp.admin_share_pct}%</span>
                                                            <span>Sinking: {grp.sinking_share_pct}%</span>
                                                        </div>
                                                    </div>
                                                </AccordionTrigger>
                                                <AccordionContent className="px-4 pb-4 space-y-3">
                                                    <div className="grid grid-cols-2 gap-3">
                                                        <div>
                                                            <Label className="text-xs">Group Name</Label>
                                                            <Input
                                                                value={grp.group_name}
                                                                onChange={e => updateGroup(idx, {group_name: e.target.value})}
                                                            />
                                                        </div>
                                                        <div>
                                                            <Label className="text-xs">Colour</Label>
                                                            <div className="flex gap-2 items-center mt-1">
                                                                <input
                                                                    type="color"
                                                                    value={grp.color}
                                                                    onChange={e => updateGroup(idx, {color: e.target.value})}
                                                                    className="h-9 w-14 border rounded cursor-pointer"
                                                                />
                                                                <span
                                                                    className="text-xs text-muted-foreground">{grp.color}</span>
                                                            </div>
                                                        </div>
                                                    </div>

                                                    <div className="grid grid-cols-2 gap-3">
                                                        <div>
                                                            <Label className="text-xs">Admin Share %</Label>
                                                            <Input
                                                                type="number"
                                                                step="0.01"
                                                                min="0"
                                                                max="100"
                                                                value={grp.admin_share_pct}
                                                                onChange={e => updateGroup(idx, {admin_share_pct: parseFloat(e.target.value) || 0})}
                                                            />
                                                        </div>
                                                        <div>
                                                            <Label className="text-xs">Sinking Share %</Label>
                                                            <Input
                                                                type="number"
                                                                step="0.01"
                                                                min="0"
                                                                max="100"
                                                                value={grp.sinking_share_pct}
                                                                onChange={e => updateGroup(idx, {sinking_share_pct: parseFloat(e.target.value) || 0})}
                                                            />
                                                        </div>
                                                    </div>

                                                    <div className="grid grid-cols-2 gap-3">
                                                        <div>
                                                            <Label className="text-xs">Unit Number Prefixes
                                                                (auto-match)</Label>
                                                            <Input
                                                                placeholder="e.g. UA, TH (comma separated)"
                                                                value={(grp.unit_type_prefixes || []).join(", ")}
                                                                onChange={e => updateGroup(idx, {
                                                                    unit_type_prefixes: e.target.value.split(",").map(s => s.trim()).filter(Boolean),
                                                                })}
                                                            />
                                                            <p className="text-xs text-muted-foreground mt-1">UA =
                                                                Apartments, TH = Townhouses</p>
                                                        </div>
                                                        <div>
                                                            <Label className="text-xs">Explicit Unit Numbers</Label>
                                                            <Input
                                                                placeholder="e.g. UA001, TH002 (comma separated)"
                                                                value={(grp.unit_numbers || []).join(", ")}
                                                                onChange={e => updateGroup(idx, {
                                                                    unit_numbers: e.target.value.split(",").map(s => s.trim()).filter(Boolean),
                                                                })}
                                                            />
                                                        </div>
                                                    </div>

                                                    {/* UOE Overrides */}
                                                    <div>
                                                        <Label className="text-xs">UOE Overrides (optional)</Label>
                                                        <p className="text-xs text-muted-foreground mb-2">
                                                            Adjust individual unit entitlement values for this scenario.
                                                            Total UOE across all groups must equal the original total.
                                                        </p>
                                                        {(grp.uoe_overrides || []).map((ov, oidx) => (
                                                            <div key={oidx} className="flex gap-2 mb-1 items-center">
                                                                <Input
                                                                    className="h-8 text-xs"
                                                                    placeholder="Unit number"
                                                                    value={ov.unit_number}
                                                                    onChange={e => {
                                                                        const ovs = [...(grp.uoe_overrides || [])];
                                                                        ovs[oidx] = {
                                                                            ...ov,
                                                                            unit_number: e.target.value
                                                                        };
                                                                        updateGroup(idx, {uoe_overrides: ovs});
                                                                    }}
                                                                />
                                                                <Input
                                                                    className="h-8 text-xs w-28"
                                                                    type="number"
                                                                    step="0.0001"
                                                                    placeholder="New UOE"
                                                                    value={ov.adjusted_uoe}
                                                                    onChange={e => {
                                                                        const ovs = [...(grp.uoe_overrides || [])];
                                                                        ovs[oidx] = {
                                                                            ...ov,
                                                                            adjusted_uoe: parseFloat(e.target.value) || 0
                                                                        };
                                                                        updateGroup(idx, {uoe_overrides: ovs});
                                                                    }}
                                                                />
                                                                <Button
                                                                    variant="ghost"
                                                                    size="sm"
                                                                    className="text-red-500 h-8 px-2"
                                                                    onClick={() => {
                                                                        const ovs = (grp.uoe_overrides || []).filter((_, i) => i !== oidx);
                                                                        updateGroup(idx, {uoe_overrides: ovs});
                                                                    }}
                                                                >
                                                                    <Trash2 className="h-3 w-3"/>
                                                                </Button>
                                                            </div>
                                                        ))}
                                                        <Button
                                                            variant="outline"
                                                            size="sm"
                                                            className="h-7 text-xs mt-1"
                                                            onClick={() => updateGroup(idx, {
                                                                uoe_overrides: [...(grp.uoe_overrides || []), {
                                                                    unit_number: "",
                                                                    adjusted_uoe: 0
                                                                }],
                                                            })}
                                                        >
                                                            <Plus className="h-3 w-3 mr-1"/> Add UOE Override
                                                        </Button>
                                                    </div>

                                                    {editorGroups.length > 1 && (
                                                        <div className="pt-2 border-t">
                                                            <Button
                                                                variant="ghost"
                                                                size="sm"
                                                                className="text-red-500 hover:text-red-700 h-7 text-xs"
                                                                onClick={() => removeGroup(idx)}
                                                            >
                                                                <Trash2 className="h-3 w-3 mr-1"/> Remove Group
                                                            </Button>
                                                        </div>
                                                    )}
                                                </AccordionContent>
                                            </AccordionItem>
                                        ))}
                                    </Accordion>

                                    {/* Save + Compute */}
                                    <div className="flex gap-3 pt-2">
                                        {canManage && (
                                            <>
                                                <Button variant="outline" onClick={handleSaveEditor}
                                                        disabled={isSaving || !sharesValid}>
                                                    {isSaving ?
                                                        <RefreshCw className="h-4 w-4 animate-spin mr-1"/> : null}
                                                    Save Draft
                                                </Button>
                                                <Button
                                                    className="bg-primary hover:bg-primary/90"
                                                    onClick={handleCompute}
                                                    disabled={isComputing || !sharesValid}
                                                >
                                                    {isComputing
                                                        ? <RefreshCw className="h-4 w-4 animate-spin mr-1"/>
                                                        : <Play className="h-4 w-4 mr-1"/>}
                                                    Compute Levies
                                                </Button>
                                            </>
                                        )}
                                    </div>
                                </CardContent>
                            </Card>
                        </>
                    )}
                </TabsContent>

                {/* ── Tab 3: Results ───────────────────────────────────────────── */}
                <TabsContent value="results" className="space-y-4">
                    {!editingScenario?.last_result ? (
                        <Card>
                            <CardContent className="pt-6 text-center text-muted-foreground">
                                No results yet. Go to the Editor tab and click Compute Levies.
                            </CardContent>
                        </Card>
                    ) : (
                        <>
                            {/* Result summary cards */}
                            {(() => {
                                const r = editingScenario.last_result;
                                return (
                                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                                        <Card>
                                            <CardContent className="pt-4">
                                                <div className="text-xs text-muted-foreground">Total Admin Budget</div>
                                                <div className="text-xl font-bold">{formatMoneyFromDollars(r.grand_total_admin)}</div>
                                            </CardContent>
                                        </Card>
                                        <Card>
                                            <CardContent className="pt-4">
                                                <div className="text-xs text-muted-foreground">Total Capital Works
                                                    Budget
                                                </div>
                                                <div className="text-xl font-bold">{formatMoneyFromDollars(r.grand_total_sinking)}</div>
                                            </CardContent>
                                        </Card>
                                        <Card>
                                            <CardContent className="pt-4">
                                                <div className="text-xs text-muted-foreground">UOE Preserved</div>
                                                <div
                                                    className={`text-xl font-bold flex items-center gap-1 ${r.uoe_total_preserved ? "text-emerald-600" : "text-red-600"}`}>
                                                    {r.uoe_total_preserved
                                                        ? <><CheckCircle2 className="h-5 w-5"/> Yes</>
                                                        : <><AlertTriangle className="h-5 w-5"/> Diff {r.uoe_diff}</>}
                                                </div>
                                                <div className="text-xs text-muted-foreground mt-1">
                                                    Original: {r.total_original_uoe} · Scenario: {r.total_scenario_uoe}
                                                </div>
                                            </CardContent>
                                        </Card>
                                        <Card>
                                            <CardContent className="pt-4">
                                                <div className="text-xs text-muted-foreground">Units Computed</div>
                                                <div className="text-xl font-bold">{r.unit_results?.length ?? 0}</div>
                                                {r.unassigned_units?.length > 0 && (
                                                    <div className="text-xs text-amber-600 mt-1">
                                                        <AlertTriangle className="h-3 w-3 inline mr-1"/>
                                                        {r.unassigned_units.length} unassigned
                                                    </div>
                                                )}
                                            </CardContent>
                                        </Card>
                                    </div>
                                );
                            })()}

                            {/* Group summary */}
                            {editingScenario.last_result.group_summaries?.length > 0 && (
                                <Card>
                                    <CardHeader className="pb-2">
                                        <CardTitle className="text-sm">Group Summary</CardTitle>
                                    </CardHeader>
                                    <CardContent>
                                        <Table>
                                            <TableHeader>
                                                <TableRow>
                                                    <TableHead>Group</TableHead>
                                                    <TableHead className="text-right">Units</TableHead>
                                                    <TableHead className="text-right">Admin %</TableHead>
                                                    <TableHead className="text-right">Sinking %</TableHead>
                                                    <TableHead className="text-right">Group Total Levy</TableHead>
                                                    <TableHead className="text-right">Avg per Unit</TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {editingScenario.last_result.group_summaries.map((g: any) => (
                                                    <TableRow key={g.group_name}>
                                                        <TableCell>
                                                            <div className="flex items-center gap-2">
                                                                <div className="w-3 h-3 rounded-full"
                                                                     style={{backgroundColor: g.group_color}}/>
                                                                {g.group_name}
                                                            </div>
                                                        </TableCell>
                                                        <TableCell className="text-right">{g.unit_count}</TableCell>
                                                        <TableCell
                                                            className="text-right">{g.admin_share_pct}%</TableCell>
                                                        <TableCell
                                                            className="text-right">{g.sinking_share_pct}%</TableCell>
                                                        <TableCell
                                                            className="text-right font-medium">{formatMoneyFromDollars(g.group_total_levy)}</TableCell>
                                                        <TableCell
                                                            className="text-right">{formatMoneyFromDollars(g.avg_levy_per_unit)}</TableCell>
                                                    </TableRow>
                                                ))}
                                            </TableBody>
                                        </Table>
                                    </CardContent>
                                </Card>
                            )}

                            {/* Per-unit results */}
                            <Card>
                                <CardHeader className="pb-2">
                                    <div className="flex items-center justify-between flex-wrap gap-2">
                                        <CardTitle className="text-sm">Per-Unit Results</CardTitle>
                                        <div className="flex gap-2 items-center flex-wrap">
                                            <Select value={resultFilter} onValueChange={setResultFilter}>
                                                <SelectTrigger className="h-8 text-xs w-36">
                                                    <SelectValue placeholder="Filter"/>
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="all">All Units</SelectItem>
                                                    <SelectItem value="rising">Rising Levies</SelectItem>
                                                    <SelectItem value="falling">Falling Levies</SelectItem>
                                                    {groupNames.map(n => (
                                                        <SelectItem key={n} value={n}>{n}</SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                            <Select value={resultSort} onValueChange={v => setResultSort(v as any)}>
                                                <SelectTrigger className="h-8 text-xs w-32">
                                                    <SelectValue placeholder="Sort by"/>
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="unit">Unit Number</SelectItem>
                                                    <SelectItem value="delta">Largest Change</SelectItem>
                                                    <SelectItem value="group">Group</SelectItem>
                                                </SelectContent>
                                            </Select>
                                            <Button variant="outline" size="sm" className="h-8 text-xs"
                                                    onClick={exportCSV}>
                                                <Download className="h-3 w-3 mr-1"/> CSV
                                            </Button>
                                        </div>
                                    </div>
                                </CardHeader>
                                <CardContent>
                                    <div className="overflow-x-auto">
                                        <Table>
                                            <TableHeader>
                                                <TableRow>
                                                    <TableHead>Unit</TableHead>
                                                    <TableHead>Group</TableHead>
                                                    <TableHead className="text-right">UOE</TableHead>
                                                    <TableHead className="text-right">Current Total</TableHead>
                                                    <TableHead className="text-right">Scenario Total</TableHead>
                                                    <TableHead className="text-right">Change</TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {unitResults.map(u => (
                                                    <TableRow key={u.unit_number}>
                                                        <TableCell
                                                            className="font-medium font-mono text-sm">{u.unit_number}</TableCell>
                                                        <TableCell>
                                                            <div className="flex items-center gap-1">
                                                                <div className="w-2 h-2 rounded-full"
                                                                     style={{backgroundColor: u.group_color}}/>
                                                                <span className="text-xs">{u.group_name}</span>
                                                            </div>
                                                            {u.uoe_changed && (
                                                                <div className="text-xs text-primary mt-0.5">
                                                                    UOE: {u.original_uoe.toFixed(4)} → {u.scenario_uoe.toFixed(4)}
                                                                </div>
                                                            )}
                                                        </TableCell>
                                                        <TableCell className="text-right text-xs">
                                                            {u.uoe_changed
                                                                ? <span
                                                                    className="text-primary font-medium">{u.scenario_uoe.toFixed(4)}</span>
                                                                : u.original_uoe.toFixed(4)}
                                                        </TableCell>
                                                        <TableCell
                                                            className="text-right">{formatMoneyFromDollars(u.current_total_levy)}</TableCell>
                                                        <TableCell
                                                            className="text-right font-medium">{formatMoneyFromDollars(u.scenario_total_levy)}</TableCell>
                                                        <TableCell className="text-right">
                                                            <DeltaBadge delta={u.total_delta} pct={u.total_delta_pct}/>
                                                        </TableCell>
                                                    </TableRow>
                                                ))}
                                            </TableBody>
                                        </Table>
                                    </div>

                                    {/* Apply UOE section */}
                                    {canApplyUoe && (editingScenario.last_result.unit_results ?? []).some((u: UnitResult) => u.uoe_changed) && (
                                        <div className="mt-4 p-4 rounded-lg border border-amber-200 bg-amber-50">
                                            <div className="flex items-start gap-3">
                                                <ShieldAlert className="h-5 w-5 text-amber-600 shrink-0 mt-0.5"/>
                                                <div className="flex-1">
                                                    <div className="font-medium text-amber-800 text-sm">Apply UOE
                                                        Changes to Units
                                                    </div>
                                                    <div className="text-xs text-amber-700 mt-1">
                                                        This scenario contains UOE overrides. Clicking <strong>Apply UOE
                                                        Changes</strong> will
                                                        permanently update the entitlement field in the units
                                                        collection.
                                                        This action requires a formal special resolution and is
                                                        irreversible without manual
                                                        correction. Only proceed if a resolution has been passed at an
                                                        AGM or EGM.
                                                    </div>
                                                    <div className="text-xs text-amber-700 mt-1">
                                                        UOE preserved: {editingScenario.last_result.uoe_total_preserved
                                                        ? "✓ Total UOE unchanged"
                                                        : `⚠ Diff = ${editingScenario.last_result.uoe_diff} (must be fixed before applying)`}
                                                    </div>
                                                </div>
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    className="border-amber-400 text-amber-800 hover:bg-amber-100 shrink-0"
                                                    disabled={!editingScenario.last_result.uoe_total_preserved}
                                                    onClick={() => setApplyConfirmOpen(true)}
                                                >
                                                    Apply UOE Changes
                                                </Button>
                                            </div>
                                        </div>
                                    )}
                                </CardContent>
                            </Card>
                        </>
                    )}
                </TabsContent>
            </Tabs>

            {/* ── Create Scenario Dialog ─────────────────────────────────────────── */}
            <Dialog open={createOpen} onOpenChange={v => !v && setCreateOpen(false)}>
                <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>New Levy Scenario</DialogTitle>
                        <DialogDescription>
                            Model a what-if levy allocation. You can edit the details further after creating.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <Label>Scenario Name</Label>
                                <Input
                                    value={createForm.scenario_name}
                                    onChange={e => setCreateForm(f => ({...f, scenario_name: e.target.value}))}
                                    placeholder="e.g. Apartments pay more"
                                />
                            </div>
                            <div>
                                <Label>Financial Year</Label>
                                <Select
                                    value={createForm.financial_year}
                                    onValueChange={fy => {
                                        setCreateForm(f => ({...f, financial_year: fy}));
                                        loadBaseData(fy);
                                    }}
                                >
                                    <SelectTrigger><SelectValue placeholder="Select year"/></SelectTrigger>
                                    <SelectContent>
                                        {(baseData?.available_years ?? [createForm.financial_year]).map(y => (
                                            <SelectItem key={y} value={y}>{y}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>
                        <div>
                            <Label>Description (optional)</Label>
                            <Textarea
                                value={createForm.description}
                                onChange={e => setCreateForm(f => ({...f, description: e.target.value}))}
                                placeholder="What hypothesis are you testing?"
                                rows={2}
                            />
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <Label>Admin Fund Budget (AUD)</Label>
                                <Input
                                    type="number"
                                    step="0.01"
                                    value={createForm.total_admin_budget}
                                    onChange={e => setCreateForm(f => ({
                                        ...f,
                                        total_admin_budget: parseFloat(e.target.value) || 0
                                    }))}
                                />
                            </div>
                            <div>
                                <Label>Sinking Fund Budget (AUD)</Label>
                                <Input
                                    type="number"
                                    step="0.01"
                                    value={createForm.total_sinking_budget}
                                    onChange={e => setCreateForm(f => ({
                                        ...f,
                                        total_sinking_budget: parseFloat(e.target.value) || 0
                                    }))}
                                />
                            </div>
                        </div>

                        {/* Quick group setup */}
                        <div>
                            <div className="flex items-center justify-between mb-2">
                                <Label>Groups ({createForm.groups.length})</Label>
                                <div className="flex gap-2">
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-7 text-xs"
                                        onClick={() => {
                                            const n = createForm.groups.length;
                                            const share = parseFloat((100 / (n + 1)).toFixed(2));
                                            const rem = parseFloat((100 - share * n).toFixed(2));
                                            setCreateForm(f => ({
                                                ...f,
                                                groups: [
                                                    ...f.groups.map(g => ({
                                                        ...g,
                                                        admin_share_pct: share,
                                                        sinking_share_pct: share
                                                    })),
                                                    {
                                                        ...newGroup(n, n + 1),
                                                        admin_share_pct: rem,
                                                        sinking_share_pct: rem
                                                    },
                                                ],
                                            }));
                                        }}
                                    >
                                        <Plus className="h-3 w-3 mr-1"/> Add Group
                                    </Button>
                                </div>
                            </div>
                            {createForm.groups.map((g, i) => (
                                <div key={i} className="flex gap-2 mb-2 items-center">
                                    <div className="w-3 h-3 rounded-full shrink-0" style={{backgroundColor: g.color}}/>
                                    <Input
                                        className="h-8 text-xs"
                                        placeholder={`Group name`}
                                        value={g.group_name}
                                        onChange={e => setCreateForm(f => ({
                                            ...f,
                                            groups: f.groups.map((gg, ii) => ii === i ? {
                                                ...gg,
                                                group_name: e.target.value
                                            } : gg),
                                        }))}
                                    />
                                    <Input
                                        className="h-8 text-xs w-24"
                                        placeholder="Prefixes e.g. UA"
                                        value={(g.unit_type_prefixes || []).join(",")}
                                        onChange={e => setCreateForm(f => ({
                                            ...f,
                                            groups: f.groups.map((gg, ii) => ii === i
                                                ? {
                                                    ...gg,
                                                    unit_type_prefixes: e.target.value.split(",").map(s => s.trim()).filter(Boolean)
                                                }
                                                : gg),
                                        }))}
                                    />
                                    <Input
                                        className="h-8 text-xs w-24"
                                        type="number"
                                        step="0.01"
                                        placeholder="Admin %"
                                        value={g.admin_share_pct}
                                        onChange={e => setCreateForm(f => ({
                                            ...f,
                                            groups: f.groups.map((gg, ii) => ii === i
                                                ? {...gg, admin_share_pct: parseFloat(e.target.value) || 0}
                                                : gg),
                                        }))}
                                    />
                                    <Input
                                        className="h-8 text-xs w-24"
                                        type="number"
                                        step="0.01"
                                        placeholder="Sinking %"
                                        value={g.sinking_share_pct}
                                        onChange={e => setCreateForm(f => ({
                                            ...f,
                                            groups: f.groups.map((gg, ii) => ii === i
                                                ? {...gg, sinking_share_pct: parseFloat(e.target.value) || 0}
                                                : gg),
                                        }))}
                                    />
                                    {createForm.groups.length > 1 && (
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            className="h-8 px-2 text-red-500"
                                            onClick={() => setCreateForm(f => ({
                                                ...f,
                                                groups: f.groups.filter((_, ii) => ii !== i)
                                            }))}
                                        >
                                            <Trash2 className="h-3 w-3"/>
                                        </Button>
                                    )}
                                </div>
                            ))}
                            <div className={`text-xs mt-1 ${createSharesValid ? "text-emerald-600" : "text-red-600"}`}>
                                Admin total: {createAdminTotal.toFixed(2)}% · Sinking
                                total: {createSinkingTotal.toFixed(2)}%
                                {createSharesValid ? " ✓" : " (must equal 100%)"}
                            </div>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button>
                        <Button
                            onClick={handleCreate}
                            disabled={creating || !createForm.scenario_name || !createSharesValid}
                        >
                            {creating ? <RefreshCw className="h-4 w-4 animate-spin mr-1"/> : null}
                            Create Scenario
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* ── Apply UOE Confirm Dialog ──────────────────────────────────────── */}
            <Dialog open={applyConfirmOpen} onOpenChange={v => !v && setApplyConfirmOpen(false)}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2 text-amber-700">
                            <ShieldAlert className="h-5 w-5"/> Confirm Apply UOE Changes
                        </DialogTitle>
                        <DialogDescription>
                            This will permanently update the unit entitlement (UOE) values in the database
                            for all units with overrides in this scenario.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="text-sm space-y-2 text-foreground">
                        <p>
                            <strong>This change is irreversible</strong> without manually editing each unit.
                            It should only be performed after a formal special resolution has been passed at an AGM or
                            EGM.
                        </p>
                        <p>
                            Units affected:{" "}
                            <strong>
                                {(editingScenario?.last_result?.unit_results ?? []).filter((u: UnitResult) => u.uoe_changed).length}
                            </strong>
                        </p>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setApplyConfirmOpen(false)}>Cancel</Button>
                        <Button
                            className="bg-amber-600 hover:bg-amber-700"
                            onClick={handleApplyUoe}
                            disabled={applyingUoe}
                        >
                            {applyingUoe ? <RefreshCw className="h-4 w-4 animate-spin mr-1"/> : null}
                            Confirm Apply UOE Changes
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
