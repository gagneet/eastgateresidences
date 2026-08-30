// @featuretrace:capital-works-intelligence — Capital Works Planner: 10-year capital works
//   schedule with cost forecasting and levy-impact modelling.
// Layer: frontend
// Data flow: this page → GET /intelligence/capital-works, GET /finance/sinking-fund-plan →
//            services/capital_shock_service.py (building-scoped).
// Related: frontend/src/pages/dashboard/CapitalRiskPage.jsx
//           backend/routers/intelligence.py
//           backend/services/capital_shock_service.py
import { useCallback, useEffect, useState } from "react";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from "@/components/ui/table";
import { useRouter } from "next/navigation";
import { useAuth } from "../../contexts/AuthContext";
import { toast } from "sonner";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, } from "recharts";

const EDIT_ROLES = ["super_admin", "strata_manager", "ec_member"];

const SEVERITY_STYLES = {
    critical: "bg-rose-100 text-rose-700",
    high: "bg-orange-100 text-orange-700",
    medium: "bg-amber-100 text-amber-700",
    low: "bg-emerald-100 text-emerald-700",
};
/**
 * @generated FunctionHeader
 * Function: fmt
 * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function fmt(n) {
    return Number(n || 0).toLocaleString();
}
/**
 * @generated FunctionHeader
 * Function: Badge
 * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function Badge({label, className}) {
    return (
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${className}`}>{label}</span>
    );
}
/**
 * @generated FunctionHeader
 * Function: KpiCard
 * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function KpiCard({label, value}) {
    return (
        <div className="bg-card border rounded-xl p-4 shadow-sm">
            <div className="text-xs text-muted-foreground mb-1">{label}</div>
            <div className="text-xl font-bold text-foreground">{value}</div>
        </div>
    );
}
/**
 * The ten-year plan measured against ten years of sinking levy, per benefit group.
 *
 * This is a SOLVENCY panel on a planning page, and it sits above the plan deliberately:
 * whether the plan is funded at all precedes any question about how its cost is shared.
 *
 * Two things it must never do. It must not render a missing position as a funded one --
 * an absent `capital_outlook` returns null and the page simply carries on without the
 * panel. And it must not present a position the engine declined to stand behind, so a
 * non-ready status renders the reasons instead of the numbers: while a capital item
 * names an asset that is not in the register, its cost is being spread across lots the
 * work may not serve, and every per-group figure below would inherit that.
 */
function FundingPositionCard({position}) {
    if (!position) return null;
    const outlook = position.capital_outlook;
    const notReady = ( position.missing_inputs || [] ).length > 0;

    if (notReady) {
        return (
            <div className="rounded-xl border border-amber-300 bg-amber-50 p-4">
                <div className="text-sm font-semibold text-amber-900">
                    Funding position not shown — the capital plan has unresolved inputs
                </div>
                <ul className="mt-2 space-y-1">
                    {position.missing_inputs.map((k) => (
                        <li key={k} className="text-xs text-amber-900 font-mono">{k}</li>
                    ))}
                </ul>
            </div>
        );
    }
    if (!outlook) return null;

    const gap = outlook.funding_gap ?? 0;
    const short = gap > 0;
    return (
        <div className="rounded-xl border bg-card p-5 shadow-sm space-y-4">
            <div className="flex items-baseline justify-between flex-wrap gap-2">
                <div>
                    <div className="text-sm font-semibold text-foreground">
                        Funding position — {outlook.first_year}–{outlook.last_year}
                    </div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                        Planned works measured against sinking levy raised over the same
                        {" "}{outlook.horizon_years} year(s)
                    </div>
                </div>
                <div className="text-right">
                    <div className={`text-2xl font-bold ${short ? "text-rose-600" : "text-emerald-600"}`}>
                        {short ? "-" : "+"}${fmt(Math.abs(gap))}
                    </div>
                    <div className="text-xs text-muted-foreground">
                        {short ? "short over the plan" : "surplus over the plan"}
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
                <KpiCard label="Planned capital works" value={`$${fmt(outlook.planned_total)}`}/>
                <KpiCard label="Sinking levy over the horizon" value={`$${fmt(outlook.sinking_total)}`}/>
            </div>

            {( outlook.groups || [] ).length > 0 && (
                <div className="space-y-2">
                    <div className="text-xs font-medium text-muted-foreground uppercase">
                        Attributed to each group
                    </div>
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                            <tr className="text-xs text-muted-foreground border-b">
                                <th className="text-left py-1.5 font-medium">Group</th>
                                <th className="text-right py-1.5 font-medium">Works attributed</th>
                                <th className="text-right py-1.5 font-medium">Sinking contributed</th>
                                <th className="text-right py-1.5 font-medium">Difference</th>
                            </tr>
                            </thead>
                            <tbody>
                            {outlook.groups.map((g) => (
                                <tr key={g.group} className="border-b last:border-0">
                                    <td className="py-1.5 font-medium text-foreground">{g.group}</td>
                                    <td className="py-1.5 text-right tabular-nums">${fmt(g.planned_spend)}</td>
                                    <td className="py-1.5 text-right tabular-nums">${fmt(g.sinking_contribution)}</td>
                                    <td className={`py-1.5 text-right tabular-nums font-medium ${
                                        g.delta < 0 ? "text-rose-600" : "text-emerald-600"}`}>
                                        {g.delta < 0 ? "-" : "+"}${fmt(Math.abs(g.delta))}
                                    </td>
                                </tr>
                            ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="text-xs text-muted-foreground">
                        {/* Stated because a reader who has just seen a zero-sum
                            redistribution will expect these to net, and reading a real
                            shortfall as an arithmetic fault is the wrong conclusion. */}
                        These differences do not net to zero. Planned spend and planned
                        contributions are independent figures, so what is left over is the
                        funding gap above, not a rounding residual.
                    </p>
                </div>
            )}
        </div>
    );
}

// Recompute closing balances from a draft plan array
/**
 * @generated FunctionHeader
 * Function: recomputeBalances
 * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function recomputeBalances(plan) {
    let balance = plan[ 0 ]?.opening_balance ?? 0;
    return plan.map((row) => {
        const opening = balance;
        const closing = opening + ( row.contribution || 0 ) - ( row.expenditure || 0 );
        balance = closing;
        return {...row, opening_balance: opening, closing_balance: closing};
    });
}
/**
 * @generated FunctionHeader
 * Function: CapitalWorksPlanner
 * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function CapitalWorksPlanner() {
    const {user, api} = useAuth();
    const router = useRouter();
    const canEdit = EDIT_ROLES.includes(user?.role);

    const [sfPlan, setSfPlan] = useState(null);
    const [capitalWorks, setCapitalWorks] = useState([]);
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState("sinking-fund");

    // Sinking fund plan edit state
    const [editingSF, setEditingSF] = useState(false);
    const [planDraft, setPlanDraft] = useState([]);
    const [saving, setSaving] = useState(false);

    // Capital events edit state
    const [eventsDraft, setEventsDraft] = useState([]);
    const [savingEvents, setSavingEvents] = useState(false);
    const [editingEvents, setEditingEvents] = useState(false);
    const [eventDialog, setEventDialog] = useState(null); // {mode, data, index}

    // Capital works edit state
    const [worksDraft, setWorksDraft] = useState([]);
    const [savingWorks, setSavingWorks] = useState(false);
    const [editingWorks, setEditingWorks] = useState(false);
    const [worksDialog, setWorksDialog] = useState(null); // {mode, data, index}
    const [fundingPosition, setFundingPosition] = useState(null);

    const fetchData = useCallback(async () => {
        setLoading(true);
        try {
            const [sfRes, cwRes, fpRes] = await Promise.allSettled([
                api.get("/finance/sinking-fund-plan"),
                api.get("/intelligence/capital-works"),
                api.get("/intelligence/capital-works/funding-position"),
            ]);
            if (sfRes.status === "fulfilled") setSfPlan(sfRes.value.data);
            if (cwRes.status === "fulfilled") setCapitalWorks(cwRes.value.data || []);
            // allSettled, and the position is rendered only when it arrives: a plan
            // page must still render its plan when the funding position cannot be
            // computed. An absent position is not a funded one.
            if (fpRes.status === "fulfilled") setFundingPosition(fpRes.value.data || null);
        } catch (err) {
            toast.error("Failed to load capital works data");
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);
    // ── Sinking Fund Plan edit helpers ──────────────────────────────────────────

    /**
     * @generated FunctionHeader
     * Function: startEditSF
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function startEditSF() {
        setPlanDraft(sfPlan?.plan ? JSON.parse(JSON.stringify(sfPlan.plan)) : []);
        setEditingSF(true);
    }
    /**
     * @generated FunctionHeader
     * Function: cancelEditSF
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function cancelEditSF() {
        setEditingSF(false);
        setPlanDraft([]);
    }
    /**
     * @generated FunctionHeader
     * Function: updateDraftRow
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function updateDraftRow(idx, field, value) {
        setPlanDraft((prev) => {
            const next = [...prev];
            next[ idx ] = {...next[ idx ], [ field ]: parseFloat(value) || 0};
            return recomputeBalances(next);
        });
    }
    /**
     * @generated FunctionHeader
     * Function: saveSFPlan
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    async function saveSFPlan() {
        setSaving(true);
        try {
            await api.put("/finance/sinking-fund-plan", {plan: planDraft});
            toast.success("Sinking fund plan saved");
            setEditingSF(false);
            await fetchData();
        } catch (err) {
            toast.error(err?.response?.data?.detail || "Save failed");
        } finally {
            setSaving(false);
        }
    }
    // ── Capital Events edit helpers ──────────────────────────────────────────────

    /**
     * @generated FunctionHeader
     * Function: startEditEvents
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function startEditEvents() {
        setEventsDraft(sfPlan?.capital_events ? JSON.parse(JSON.stringify(sfPlan.capital_events)) : []);
        setEditingEvents(true);
    }
    /**
     * @generated FunctionHeader
     * Function: cancelEditEvents
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function cancelEditEvents() {
        setEditingEvents(false);
        setEventsDraft([]);
        setEventDialog(null);
    }
    /**
     * @generated FunctionHeader
     * Function: openEventDialog
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function openEventDialog(mode, data = {}, index = null) {
        setEventDialog({
            mode,
            data: {year: "", label: "", amount: "", severity: "medium", description: "", ...data},
            index
        });
    }
    /**
     * @generated FunctionHeader
     * Function: saveEventDialog
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function saveEventDialog() {
        const {mode, data, index} = eventDialog;
        const event = {...data, year: parseInt(data.year), amount: parseFloat(data.amount) || 0};
        if (!event.label || !event.year) {
            toast.error("Year and Label are required");
            return;
        }
        setEventsDraft((prev) => {
            const next = [...prev];
            if (mode === "add") next.push(event);
            else next[ index ] = event;
            return next.sort((a, b) => a.year - b.year);
        });
        setEventDialog(null);
    }
    /**
     * @generated FunctionHeader
     * Function: deleteEvent
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function deleteEvent(index) {
        setEventsDraft((prev) => prev.filter((_, i) => i !== index));
    }
    /**
     * @generated FunctionHeader
     * Function: saveEvents
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    async function saveEvents() {
        setSavingEvents(true);
        try {
            await api.put("/finance/sinking-fund-capital-events", {events: eventsDraft});
            toast.success("Capital events saved");
            setEditingEvents(false);
            await fetchData();
        } catch (err) {
            toast.error(err?.response?.data?.detail || "Save failed");
        } finally {
            setSavingEvents(false);
        }
    }
    // ── Capital Works edit helpers ───────────────────────────────────────────────

    /**
     * @generated FunctionHeader
     * Function: startEditWorks
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function startEditWorks() {
        setWorksDraft(capitalWorks ? JSON.parse(JSON.stringify(capitalWorks)) : []);
        setEditingWorks(true);
    }
    /**
     * @generated FunctionHeader
     * Function: cancelEditWorks
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function cancelEditWorks() {
        setEditingWorks(false);
        setWorksDraft([]);
        setWorksDialog(null);
    }
    /**
     * @generated FunctionHeader
     * Function: openWorksDialog
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function openWorksDialog(mode, data = {}, index = null) {
        setWorksDialog({
            mode,
            data: {replacement_year: "", asset_name: "", category: "", estimated_cost: "", ...data},
            index
        });
    }
    /**
     * @generated FunctionHeader
     * Function: saveWorksDialog
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function saveWorksDialog() {
        const {mode, data, index} = worksDialog;
        const item = {
            ...data,
            replacement_year: parseInt(data.replacement_year),
            estimated_cost: parseFloat(data.estimated_cost) || 0
        };
        if (!item.asset_name || !item.replacement_year) {
            toast.error("Year and Asset Name are required");
            return;
        }
        setWorksDraft((prev) => {
            const next = [...prev];
            if (mode === "add") next.push(item);
            else next[ index ] = item;
            return next.sort((a, b) => a.replacement_year - b.replacement_year);
        });
        setWorksDialog(null);
    }
    /**
     * @generated FunctionHeader
     * Function: deleteWork
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function deleteWork(index) {
        setWorksDraft((prev) => prev.filter((_, i) => i !== index));
    }
    /**
     * @generated FunctionHeader
     * Function: saveWorks
     * Path: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    async function saveWorks() {
        setSavingWorks(true);
        try {
            await api.put("/intelligence/capital-works", {items: worksDraft});
            toast.success("Replacement schedule saved");
            setEditingWorks(false);
            await fetchData();
        } catch (err) {
            toast.error(err?.response?.data?.detail || "Save failed");
        } finally {
            setSavingWorks(false);
        }
    }

    // ── Render ───────────────────────────────────────────────────────────────────

    if (loading) {
        return <div className="p-8 text-center text-muted-foreground">Loading Capital Works Planner...</div>;
    }

    const summary = sfPlan?.summary || {};
    const plan = sfPlan?.plan || [];
    const events = sfPlan?.capital_events || [];
    const chartData = plan.filter((r) => r.year >= 2024);

    const tabs = [
        {id: "sinking-fund", label: "Sinking Fund Plan"},
        {id: "events", label: "Capital Event Milestones"},
        {id: "schedule", label: "10-Year Replacement Schedule"},
    ];

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex flex-wrap gap-3 items-start justify-between">
                <div>
                    <button
                        onClick={() => router.push("/intelligence/building")}
                        className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-2 -ml-1"
                    >
                        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7"/>
                        </svg>
                        Back to Intelligence Hub
                    </button>
                    <h1 className="text-3xl font-bold tracking-tight">Capital Works Planner</h1>
                    <p className="text-muted-foreground mt-1">15-year sinking fund plan, capital event milestones, and 10-year
                        asset replacement schedule.</p>
                </div>
            </div>

            <FundingPositionCard position={fundingPosition}/>

            {/* KPI Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <KpiCard label="Total 15yr Contributions" value={`$${fmt(summary.total_contributions)}`}/>
                <KpiCard label="Total 15yr Expenditures" value={`$${fmt(summary.total_expenditures)}`}/>
                <KpiCard label="Projected Balance (2035)" value={`$${fmt(summary.closing_balance_2035)}`}/>
                <KpiCard
                    label="Major Capital Years"
                    value={( summary.major_capital_years || [] ).join(", ") || "—"}
                />
            </div>

            {/* Tabs */}
            <div className="bg-card border rounded-xl shadow-sm overflow-hidden">
                <div className="flex border-b">
                    {tabs.map((t) => (
                        <button
                            key={t.id}
                            onClick={() => setActiveTab(t.id)}
                            className={`px-5 py-3 text-sm font-medium transition-colors ${
                                activeTab === t.id
                                    ? "border-b-2 border-primary/20 text-primary bg-primary/10"
                                    : "text-muted-foreground hover:text-foreground"
                            }`}
                        >
                            {t.label}
                        </button>
                    ))}
                </div>

                {/* ── Tab 1: Sinking Fund Plan ── */}
                {activeTab === "sinking-fund" && (
                    <div className="p-5 space-y-4">
                        <div className="flex items-center justify-between">
                            <h2 className="font-semibold text-lg">Sinking Fund Plan 2021–2035</h2>
                            {canEdit && !editingSF && (
                                <button
                                    onClick={startEditSF}
                                    className="px-4 py-1.5 text-sm bg-primary text-white rounded-lg hover:bg-primary"
                                >
                                    Edit Plan
                                </button>
                            )}
                            {editingSF && (
                                <div className="flex gap-2">
                                    <button
                                        onClick={cancelEditSF}
                                        className="px-4 py-1.5 text-sm border rounded-lg hover:bg-muted"
                                    >
                                        Cancel
                                    </button>
                                    <button
                                        onClick={saveSFPlan}
                                        disabled={saving}
                                        className="px-4 py-1.5 text-sm bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50"
                                    >
                                        {saving ? "Saving..." : "Save Changes"}
                                    </button>
                                </div>
                            )}
                        </div>

                        {/* Chart */}
                        {chartData.length > 0 && (
                            <div className="h-56">
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={chartData}>
                                        <CartesianGrid strokeDasharray="3 3" vertical={false}/>
                                        <XAxis dataKey="year" tick={{fontSize: 12}}/>
                                        <YAxis tickFormatter={(v) => `$${( v / 1000 ).toFixed(0)}k`}
                                               tick={{fontSize: 12}}/>
                                        <Tooltip formatter={(v) => [`$${Number(v).toLocaleString()}`, ""]}/>
                                        <Bar dataKey="contribution" fill="#10b981" name="Contribution"
                                             radius={[3, 3, 0, 0]}/>
                                        <Bar dataKey="expenditure" fill="#f59e0b" name="Expenditure"
                                             radius={[3, 3, 0, 0]}/>
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                        )}

                        {/* Table */}
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                <TableRow className="border-b text-left text-muted-foreground">
                                    <TableHead className="py-2 pr-4 font-medium">Year</TableHead>
                                    <TableHead className="py-2 pr-4 text-right font-medium">Contribution</TableHead>
                                    <TableHead className="py-2 pr-4 text-right font-medium">Expenditure</TableHead>
                                    <TableHead className="py-2 pr-4 text-right font-medium">Opening Balance</TableHead>
                                    <TableHead className="py-2 pr-4 text-right font-medium">Closing Balance</TableHead>
                                    <TableHead className="py-2 font-medium">Status</TableHead>
                                </TableRow>
                                </TableHeader>
                                <TableBody>
                                {( editingSF ? planDraft : plan ).map((row, idx) => (
                                    <TableRow
                                        key={row.year}
                                        className={`border-b last:border-0 ${row.is_major_capital_year ? "bg-amber-50" : ""}`}
                                    >
                                        <TableCell className="py-2 pr-4 font-bold">{row.year}</TableCell>
                                        {editingSF ? (
                                            <>
                                                <TableCell className="py-1 pr-4 text-right">
                                                    <input
                                                        type="number"
                                                        value={row.contribution || 0}
                                                        onChange={(e) => updateDraftRow(idx, "contribution", e.target.value)}
                                                        className="w-28 text-right border rounded px-2 py-0.5 text-sm"
                                                    />
                                                </TableCell>
                                                <TableCell className="py-1 pr-4 text-right">
                                                    <input
                                                        type="number"
                                                        value={row.expenditure || 0}
                                                        onChange={(e) => updateDraftRow(idx, "expenditure", e.target.value)}
                                                        className="w-28 text-right border rounded px-2 py-0.5 text-sm"
                                                    />
                                                </TableCell>
                                                <TableCell className="py-2 pr-4 text-right text-muted-foreground">${fmt(row.opening_balance)}</TableCell>
                                                <TableCell className="py-2 pr-4 text-right font-mono">${fmt(row.closing_balance)}</TableCell>
                                            </>
                                        ) : (
                                            <>
                                                <TableCell className="py-2 pr-4 text-right text-emerald-700">${fmt(row.contribution)}</TableCell>
                                                <TableCell className="py-2 pr-4 text-right text-amber-700">${fmt(row.expenditure)}</TableCell>
                                                <TableCell className="py-2 pr-4 text-right text-muted-foreground">${fmt(row.opening_balance)}</TableCell>
                                                <TableCell className="py-2 pr-4 text-right font-mono">${fmt(row.closing_balance)}</TableCell>
                                            </>
                                        )}
                                        <TableCell className="py-2 flex gap-1 flex-wrap">
                                            {row.is_actual &&
                                                <Badge label="Actual" className="bg-muted text-muted-foreground"/>}
                                            {row.is_major_capital_year &&
                                                <Badge label="Major Works" className="bg-amber-100 text-amber-700"/>}
                                        </TableCell>
                                    </TableRow>
                                ))}
                                </TableBody>
                            </Table>
                        </div>
                    </div>
                )}

                {/* ── Tab 2: Capital Event Milestones ── */}
                {activeTab === "events" && (
                    <div className="p-5 space-y-4">
                        <div className="flex items-center justify-between">
                            <h2 className="font-semibold text-lg">Capital Event Milestones</h2>
                            {canEdit && !editingEvents && (
                                <button
                                    onClick={startEditEvents}
                                    className="px-4 py-1.5 text-sm bg-primary text-white rounded-lg hover:bg-primary"
                                >
                                    Edit Events
                                </button>
                            )}
                            {editingEvents && (
                                <div className="flex gap-2">
                                    <button
                                        onClick={() => openEventDialog("add")}
                                        className="px-4 py-1.5 text-sm bg-primary/10 text-primary border border-primary/20 rounded-lg hover:bg-primary/10"
                                    >
                                        + Add Event
                                    </button>
                                    <button onClick={cancelEditEvents}
                                            className="px-4 py-1.5 text-sm border rounded-lg hover:bg-muted">
                                        Cancel
                                    </button>
                                    <button
                                        onClick={saveEvents}
                                        disabled={savingEvents}
                                        className="px-4 py-1.5 text-sm bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50"
                                    >
                                        {savingEvents ? "Saving..." : "Save Events"}
                                    </button>
                                </div>
                            )}
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                            {( editingEvents ? eventsDraft : events ).map((ev, idx) => (
                                <div key={idx} className="border rounded-lg p-4 space-y-2 relative">
                                    <div className="flex items-start justify-between gap-2">
                                        <div>
                                            <div className="font-semibold">{ev.label}</div>
                                            <div className="text-xs text-muted-foreground">Year {ev.year} ·
                                                ${fmt(ev.amount)}</div>
                                        </div>
                                        <Badge
                                            label={ev.severity || "medium"}
                                            className={SEVERITY_STYLES[ ev.severity ] || SEVERITY_STYLES.medium}
                                        />
                                    </div>
                                    {ev.description && <p className="text-xs text-muted-foreground">{ev.description}</p>}
                                    {editingEvents && (
                                        <div className="flex gap-2 pt-1">
                                            <button
                                                onClick={() => openEventDialog("edit", ev, idx)}
                                                className="text-xs text-primary hover:underline"
                                            >
                                                Edit
                                            </button>
                                            <button
                                                onClick={() => deleteEvent(idx)}
                                                className="text-xs text-rose-600 hover:underline"
                                            >
                                                Delete
                                            </button>
                                        </div>
                                    )}
                                </div>
                            ))}
                            {( editingEvents ? eventsDraft : events ).length === 0 && (
                                <p className="text-muted-foreground text-sm col-span-3 py-4">No capital event milestones
                                    defined.</p>
                            )}
                        </div>
                    </div>
                )}

                {/* ── Tab 3: 10-Year Replacement Schedule ── */}
                {activeTab === "schedule" && (
                    <div className="p-5 space-y-4">
                        <div className="flex items-center justify-between flex-wrap gap-2">
                            <h2 className="font-semibold text-lg">10-Year Asset Replacement Schedule</h2>
                            {canEdit && !editingWorks && (
                                <button
                                    onClick={startEditWorks}
                                    className="px-4 py-1.5 text-sm bg-primary text-white rounded-lg hover:bg-primary"
                                >
                                    Edit Schedule
                                </button>
                            )}
                            {editingWorks && (
                                <div className="flex gap-2">
                                    <button
                                        onClick={() => openWorksDialog("add")}
                                        className="px-4 py-1.5 text-sm bg-primary/10 text-primary border border-primary/20 rounded-lg hover:bg-primary/10"
                                    >
                                        + Add Item
                                    </button>
                                    <button onClick={cancelEditWorks}
                                            className="px-4 py-1.5 text-sm border rounded-lg hover:bg-muted">
                                        Cancel
                                    </button>
                                    <button
                                        onClick={saveWorks}
                                        disabled={savingWorks}
                                        className="px-4 py-1.5 text-sm bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50"
                                    >
                                        {savingWorks ? "Saving..." : "Save Schedule"}
                                    </button>
                                </div>
                            )}
                        </div>

                        {editingWorks && (
                            <div
                                className="text-xs text-primary bg-primary/10 border border-primary/20 rounded-lg px-4 py-2">
                                Items marked "Auto-generated" are regenerated by the Sync Engine from asset lifespans.
                                Manual edits persist until the next sync.
                            </div>
                        )}

                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                <TableRow className="border-b text-left text-muted-foreground">
                                    <TableHead className="py-2 pr-4 font-medium">Year</TableHead>
                                    <TableHead className="py-2 pr-4 font-medium">Asset Name</TableHead>
                                    <TableHead className="py-2 pr-4 font-medium">Category</TableHead>
                                    <TableHead className="py-2 pr-4 text-right font-medium">Est. Cost (CPI Adj.)</TableHead>
                                    <TableHead className="py-2 font-medium">Source</TableHead>
                                    {editingWorks && <TableHead className="py-2"/>}
                                </TableRow>
                                </TableHeader>
                                <TableBody>
                                {( editingWorks ? worksDraft : capitalWorks ).map((item, idx) => (
                                    <TableRow key={idx} className="border-b last:border-0">
                                        <TableCell className="py-2 pr-4 font-bold">{item.replacement_year}</TableCell>
                                        <TableCell className="py-2 pr-4">{item.asset_name}</TableCell>
                                        <TableCell className="py-2 pr-4 text-muted-foreground">{item.category || "—"}</TableCell>
                                        <TableCell className="py-2 pr-4 text-right font-mono text-primary font-semibold">
                                            ${fmt(item.estimated_cost)}
                                        </TableCell>
                                        <TableCell className="py-2">
                                            <Badge
                                                label={item.source === "manual" ? "Manual" : "Auto-generated"}
                                                className={item.source === "manual" ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"}
                                            />
                                        </TableCell>
                                        {editingWorks && (
                                            <TableCell className="py-2 flex gap-2">
                                                <button
                                                    onClick={() => openWorksDialog("edit", item, idx)}
                                                    className="text-xs text-primary hover:underline"
                                                >
                                                    Edit
                                                </button>
                                                <button
                                                    onClick={() => deleteWork(idx)}
                                                    className="text-xs text-rose-600 hover:underline"
                                                >
                                                    Delete
                                                </button>
                                            </TableCell>
                                        )}
                                    </TableRow>
                                ))}
                                {( editingWorks ? worksDraft : capitalWorks ).length === 0 && (
                                    <TableRow>
                                        <TableCell colSpan={6} className="text-center text-muted-foreground py-8 text-sm">
                                            No capital works scheduled. Seed data via Settings → Digital Twin.
                                        </TableCell>
                                    </TableRow>
                                )}
                                </TableBody>
                            </Table>
                        </div>
                    </div>
                )}
            </div>

            {/* ── Event Dialog ── */}
            {eventDialog && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
                    <div className="bg-card rounded-xl shadow-xl p-6 w-full max-w-md space-y-4">
                        <h3 className="font-semibold text-lg">
                            {eventDialog.mode === "add" ? "Add Capital Event" : "Edit Capital Event"}
                        </h3>
                        <div className="space-y-3">
                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <label className="text-xs font-medium text-muted-foreground">Year *</label>
                                    <input
                                        type="number"
                                        value={eventDialog.data.year}
                                        onChange={(e) => setEventDialog((d) => ( {
                                            ...d,
                                            data: {...d.data, year: e.target.value}
                                        } ))}
                                        className="w-full border rounded-lg px-3 py-2 text-sm mt-0.5"
                                        placeholder="2026"
                                    />
                                </div>
                                <div>
                                    <label className="text-xs font-medium text-muted-foreground">Amount ($)</label>
                                    <input
                                        type="number"
                                        value={eventDialog.data.amount}
                                        onChange={(e) => setEventDialog((d) => ( {
                                            ...d,
                                            data: {...d.data, amount: e.target.value}
                                        } ))}
                                        className="w-full border rounded-lg px-3 py-2 text-sm mt-0.5"
                                        placeholder="50000"
                                    />
                                </div>
                            </div>
                            <div>
                                <label className="text-xs font-medium text-muted-foreground">Label *</label>
                                <input
                                    type="text"
                                    value={eventDialog.data.label}
                                    onChange={(e) => setEventDialog((d) => ( {
                                        ...d,
                                        data: {...d.data, label: e.target.value}
                                    } ))}
                                    className="w-full border rounded-lg px-3 py-2 text-sm mt-0.5"
                                    placeholder="e.g. Roof replacement"
                                />
                            </div>
                            <div>
                                <label className="text-xs font-medium text-muted-foreground">Severity</label>
                                <select
                                    value={eventDialog.data.severity}
                                    onChange={(e) => setEventDialog((d) => ( {
                                        ...d,
                                        data: {...d.data, severity: e.target.value}
                                    } ))}
                                    className="w-full border rounded-lg px-3 py-2 text-sm mt-0.5"
                                >
                                    {["critical", "high", "medium", "low"].map((s) => (
                                        <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
                                    ))}
                                </select>
                            </div>
                            <div>
                                <label className="text-xs font-medium text-muted-foreground">Description</label>
                                <textarea
                                    value={eventDialog.data.description}
                                    onChange={(e) => setEventDialog((d) => ( {
                                        ...d,
                                        data: {...d.data, description: e.target.value}
                                    } ))}
                                    className="w-full border rounded-lg px-3 py-2 text-sm mt-0.5 resize-none"
                                    rows={2}
                                />
                            </div>
                        </div>
                        <div className="flex justify-end gap-2 pt-2">
                            <button onClick={() => setEventDialog(null)}
                                    className="px-4 py-2 text-sm border rounded-lg hover:bg-muted">
                                Cancel
                            </button>
                            <button
                                onClick={saveEventDialog}
                                className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary"
                            >
                                {eventDialog.mode === "add" ? "Add" : "Save"}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* ── Works Dialog ── */}
            {worksDialog && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
                    <div className="bg-card rounded-xl shadow-xl p-6 w-full max-w-md space-y-4">
                        <h3 className="font-semibold text-lg">
                            {worksDialog.mode === "add" ? "Add Replacement Item" : "Edit Replacement Item"}
                        </h3>
                        <div className="space-y-3">
                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <label className="text-xs font-medium text-muted-foreground">Replacement Year *</label>
                                    <input
                                        type="number"
                                        value={worksDialog.data.replacement_year}
                                        onChange={(e) => setWorksDialog((d) => ( {
                                            ...d,
                                            data: {...d.data, replacement_year: e.target.value}
                                        } ))}
                                        className="w-full border rounded-lg px-3 py-2 text-sm mt-0.5"
                                        placeholder="2027"
                                    />
                                </div>
                                <div>
                                    <label className="text-xs font-medium text-muted-foreground">Estimated Cost ($)</label>
                                    <input
                                        type="number"
                                        value={worksDialog.data.estimated_cost}
                                        onChange={(e) => setWorksDialog((d) => ( {
                                            ...d,
                                            data: {...d.data, estimated_cost: e.target.value}
                                        } ))}
                                        className="w-full border rounded-lg px-3 py-2 text-sm mt-0.5"
                                        placeholder="25000"
                                    />
                                </div>
                            </div>
                            <div>
                                <label className="text-xs font-medium text-muted-foreground">Asset Name *</label>
                                <input
                                    type="text"
                                    value={worksDialog.data.asset_name}
                                    onChange={(e) => setWorksDialog((d) => ( {
                                        ...d,
                                        data: {...d.data, asset_name: e.target.value}
                                    } ))}
                                    className="w-full border rounded-lg px-3 py-2 text-sm mt-0.5"
                                    placeholder="e.g. Main Lift (Lift 1)"
                                />
                            </div>
                            <div>
                                <label className="text-xs font-medium text-muted-foreground">Category</label>
                                <input
                                    type="text"
                                    value={worksDialog.data.category}
                                    onChange={(e) => setWorksDialog((d) => ( {
                                        ...d,
                                        data: {...d.data, category: e.target.value}
                                    } ))}
                                    className="w-full border rounded-lg px-3 py-2 text-sm mt-0.5"
                                    placeholder="e.g. Mechanical"
                                />
                            </div>
                        </div>
                        <div className="flex justify-end gap-2 pt-2">
                            <button onClick={() => setWorksDialog(null)}
                                    className="px-4 py-2 text-sm border rounded-lg hover:bg-muted">
                                Cancel
                            </button>
                            <button
                                onClick={saveWorksDialog}
                                className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary"
                            >
                                {worksDialog.mode === "add" ? "Add" : "Save"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
