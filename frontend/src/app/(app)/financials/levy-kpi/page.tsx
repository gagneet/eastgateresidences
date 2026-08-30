// @ts-nocheck
"use client";
// @featuretrace:levy-kpi — Full-page Levy KPI Analysis for management and owners.
// Layer: frontend
// Data flow: this page → /finance/levy-kpi → unit_levy_ledger + annual_levies (building-scoped).
// Related: frontend/src/components/finance/LevyKpiDialog.tsx (popup variant)
//           backend/routers/finance.py (get_levy_kpi endpoint)

import React, {useEffect, useState} from "react";
import {useAuth} from "@/contexts/AuthContext";
import {useRouter} from "next/navigation";
import {Button} from "@/components/ui/button";
import {Badge} from "@/components/ui/badge";
import {Skeleton} from "@/components/ui/skeleton";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import {
    Cell,
    Pie,
    PieChart,
    Bar,
    BarChart,
    XAxis,
    YAxis,
    CartesianGrid,
    ResponsiveContainer,
    Tooltip as RechartsTooltip,
} from "recharts";
import {formatCurrency} from "@/lib/utils";
import {
    ArrowLeft,
    TrendingUp,
    AlertCircle,
    CheckCircle2,
    Info,
    Landmark,
    DollarSign,
} from "lucide-react";
import {
    Tooltip,
    TooltipContent,
    TooltipTrigger,
} from "@/components/ui/tooltip";
import YearSelector from "@/components/widgets/YearSelector";
import {formatMoneyCompact} from '@/lib/currency';
// ── Threshold helpers ────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: collectionStatus
 * Path: frontend/src/app/(app)/financials/levy-kpi/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const collectionStatus = (rate: number) => {
    const p = rate * 100;
    if (p >= 95) return {
        color: "text-emerald-600",
        bg: "bg-emerald-50 border-emerald-200",
        bar: "bg-emerald-500",
        badge: "bg-emerald-100 text-emerald-700",
        label: "On Target"
    };
    if (p >= 85) return {
        color: "text-amber-600",
        bg: "bg-amber-50 border-amber-200",
        bar: "bg-amber-500",
        badge: "bg-amber-100 text-amber-700",
        label: "Slightly Below"
    };
    return {
        color: "text-rose-600",
        bg: "bg-rose-50 border-rose-200",
        bar: "bg-rose-500",
        badge: "bg-rose-100 text-rose-700",
        label: "At Risk"
    };
};
/**
 * @generated FunctionHeader
 * Function: adminStatus
 * Path: frontend/src/app/(app)/financials/levy-kpi/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const adminStatus = (rate: number) => {
    const p = rate * 100;
    if (p >= 100) return {
        color: "text-emerald-600",
        bg: "bg-emerald-50 border-emerald-200",
        bar: "bg-emerald-500",
        badge: "bg-emerald-100 text-emerald-700",
        label: "Healthy"
    };
    if (p >= 50) return {
        color: "text-amber-600",
        bg: "bg-amber-50 border-amber-200",
        bar: "bg-amber-500",
        badge: "bg-amber-100 text-amber-700",
        label: "Watch"
    };
    return {
        color: "text-rose-600",
        bg: "bg-rose-50 border-rose-200",
        bar: "bg-rose-500",
        badge: "bg-rose-100 text-rose-700",
        label: "At Risk"
    };
};
/**
 * @generated FunctionHeader
 * Function: sinkingStatus
 * Path: frontend/src/app/(app)/financials/levy-kpi/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const sinkingStatus = (rate: number) => {
    const p = rate * 100;
    if (p >= 200) return {
        color: "text-emerald-600",
        bg: "bg-emerald-50 border-emerald-200",
        bar: "bg-emerald-500",
        badge: "bg-emerald-100 text-emerald-700",
        label: "Liquid"
    };
    if (p >= 100) return {
        color: "text-amber-600",
        bg: "bg-amber-50 border-amber-200",
        bar: "bg-amber-500",
        badge: "bg-amber-100 text-amber-700",
        label: "Adequate"
    };
    return {
        color: "text-rose-600",
        bg: "bg-rose-50 border-rose-200",
        bar: "bg-rose-500",
        badge: "bg-rose-100 text-rose-700",
        label: "Low"
    };
};
/**
 * @generated FunctionHeader
 * Function: complianceStatus
 * Path: frontend/src/app/(app)/financials/levy-kpi/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const complianceStatus = (rate: number) => {
    const p = rate * 100;
    if (p >= 90) return {
        color: "text-emerald-600",
        bg: "bg-emerald-50 border-emerald-200",
        bar: "bg-emerald-500",
        badge: "bg-emerald-100 text-emerald-700",
        label: "Strong"
    };
    if (p >= 75) return {
        color: "text-amber-600",
        bg: "bg-amber-50 border-amber-200",
        bar: "bg-amber-500",
        badge: "bg-amber-100 text-amber-700",
        label: "Moderate"
    };
    return {
        color: "text-rose-600",
        bg: "bg-rose-50 border-rose-200",
        bar: "bg-rose-500",
        badge: "bg-rose-100 text-rose-700",
        label: "Low"
    };
};
/**
 * @generated FunctionHeader
 * Function: GaugeBar
 * Path: frontend/src/app/(app)/financials/levy-kpi/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const GaugeBar = ({value, max = 100, colorClass}: { value: number; max?: number; colorClass: string }) => (
    <div className="h-2 bg-slate-100 rounded-full overflow-hidden mt-2">
        <div className={`h-full rounded-full transition-all duration-700 ${colorClass}`}
             style={{width: `${Math.min(100, (value / max) * 100)}%`}}/>
    </div>
);
/**
 * @generated FunctionHeader
 * Function: KpiLabel
 * Path: frontend/src/app/(app)/financials/levy-kpi/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const KpiLabel = ({label, tip}: { label: string; tip: string }) => (
    <Tooltip>
        <TooltipTrigger asChild>
            <span
                className="flex items-center gap-1 cursor-help text-xs font-black text-slate-500 uppercase tracking-widest">
                {label}<Info size={11} className="text-slate-400"/>
            </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs text-xs">{tip}</TooltipContent>
    </Tooltip>
);

const PIE_COLORS = ["#10b981", "#6366f1", "#f43f5e"];
/**
 * @generated FunctionHeader
 * Function: LevyKpiPage
 * Path: frontend/src/app/(app)/financials/levy-kpi/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function LevyKpiPage() {
    const {api, selectedYear, selectedBuilding, isAdmin, isManager, isECMember} = useAuth();
    const router = useRouter();
    const [kpi, setKpi] = useState<any | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const isManagement = isAdmin() || isManager() || isECMember();

    useEffect(() => {
        setLoading(true);
        setError(null);
        api.get(`/finance/levy-kpi?year=${selectedYear || ""}`)
            .then((res) => setKpi(res.data))
            .catch(() => setError("Could not load KPI data. Ensure levy data is loaded for this building."))
            .finally(() => setLoading(false));
    }, [api, selectedYear, selectedBuilding?.id]);

    if (loading) {
        return (
            <div className="space-y-6 p-6">
                <Skeleton className="h-10 w-48"/>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-32 rounded-2xl"/>)}
                </div>
                <Skeleton className="h-64 rounded-2xl"/>
            </div>
        );
    }

    if (error || !kpi) {
        return (
            <div className="p-8 text-center space-y-4">
                <AlertCircle className="w-10 h-10 text-slate-300 mx-auto"/>
                <p className="text-slate-500">{error || "No KPI data available."}</p>
                <Button variant="outline" onClick={() => router.back()}>Go Back</Button>
            </div>
        );
    }

    const cs = collectionStatus(kpi.collection_rate ?? 0);
    const as_ = adminStatus(kpi.admin_fund_health ?? 0);
    const ss = sinkingStatus(kpi.sinking_cash_coverage ?? 0);
    const cps = complianceStatus(kpi.lot_compliance_rate ?? 0);

    const waterfallData = [
        {name: "Billed", value: kpi.quarter_billed_total_display, fill: "#6366f1"},
        {name: "Collected", value: kpi.current_quarter_collected_total, fill: "#10b981"},
        {name: "Unpaid (Q)", value: kpi.current_quarter_unpaid_total, fill: "#f59e0b"},
        {name: "True Arrears", value: kpi.true_arrears_total, fill: "#f43f5e"},
        {name: "Credits", value: kpi.credit_total, fill: "#8b5cf6"},
    ];

    const lots = kpi.lots ?? [];
    const paidExact = lots.filter((l: any) => l.status === "paid_exact").length;
    const inCredit = lots.filter((l: any) => l.status === "credit").length;
    const inArrears = lots.filter((l: any) => l.status === "arrears").length;
    const donutData = [
        {name: "Paid Exact", value: paidExact},
        {name: "In Credit", value: inCredit},
        {name: "In Arrears", value: inArrears},
    ].filter((d) => d.value > 0);

    return (
        <div className="space-y-8 pb-20">
            {/* Header */}
            <div className="flex flex-col md:flex-row justify-between items-start md:items-end gap-4">
                <div>
                    <button
                        onClick={() => router.back()}
                        className="flex items-center gap-2 text-slate-500 hover:text-slate-900 text-sm font-bold mb-3 transition-colors group"
                    >
                        <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform"/>
                        Back
                    </button>
                    <h1 className="text-3xl font-black text-slate-900 tracking-tight">Levy KPI Analysis</h1>
                    <p className="text-slate-500 mt-1">
                        Quarter-level collection metrics for {selectedBuilding?.name || "your building"} · FY {kpi.year}
                    </p>
                </div>
                <div className="flex items-center gap-3">
                    <YearSelector/>
                    <Button variant="outline" onClick={() => router.push("/financials/overview")}
                            className="rounded-xl font-bold">
                        Finance Dashboard
                    </Button>
                </div>
            </div>

            {/* ── 4 KPI Cards ── */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                {/* Collection Rate */}
                <div className={`p-5 rounded-2xl border ${cs.bg} space-y-2`}>
                    <KpiLabel label="Collection Rate"
                              tip="Percentage of the current quarter levy that has been covered, excluding prior-period arrears."/>
                    <p className={`text-3xl font-black ${cs.color}`}>{(kpi.collection_rate * 100).toFixed(2)}%</p>
                    <GaugeBar value={kpi.collection_rate * 100} colorClass={cs.bar}/>
                    <p className="text-xs text-slate-600">{formatCurrency(kpi.current_quarter_collected_total)} of {formatCurrency(kpi.quarter_billed_total_display)}</p>
                    <span
                        className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-black ${cs.badge}`}>{cs.label}</span>
                </div>

                {/* Lot Compliance */}
                <div className={`p-5 rounded-2xl border ${cps.bg} space-y-2`}>
                    <KpiLabel label="Lot Compliance"
                              tip="Lots that are either fully paid for the quarter or currently in credit."/>
                    <p className={`text-3xl font-black ${cps.color}`}>{(kpi.lot_compliance_rate * 100).toFixed(1)}%</p>
                    <GaugeBar value={kpi.lot_compliance_rate * 100} colorClass={cps.bar}/>
                    <p className="text-xs text-slate-600">{kpi.compliant_lot_count} of {kpi.total_lot_count} lots
                        compliant</p>
                    <span
                        className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-black ${cps.badge}`}>{cps.label}</span>
                </div>

                {/* Admin Fund Health */}
                <div className={`p-5 rounded-2xl border ${as_.bg} space-y-2`}>
                    <KpiLabel label="Admin Fund Health"
                              tip="Current administrative fund cash as a percentage of the next quarter's administrative budget."/>
                    <p className={`text-3xl font-black ${as_.color}`}>{(kpi.admin_fund_health * 100).toFixed(1)}%</p>
                    <GaugeBar value={kpi.admin_fund_health * 100} colorClass={as_.bar}/>
                    <p className="text-xs text-slate-600">{formatCurrency(kpi.admin_fund_balance)} vs {formatCurrency(kpi.admin_quarter_budget)} Q-budget</p>
                    <span
                        className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-black ${as_.badge}`}>{as_.label}</span>
                </div>

                {/* Sinking Cash Coverage */}
                <div className={`p-5 rounded-2xl border ${ss.bg} space-y-2`}>
                    <KpiLabel label="Sinking Coverage"
                              tip="Current sinking fund cash as a percentage of the next quarter equivalent sinking contribution. Liquidity measure, not reserve adequacy."/>
                    <p className={`text-3xl font-black ${ss.color}`}>{(kpi.sinking_cash_coverage * 100).toFixed(0)}%</p>
                    <GaugeBar value={Math.min(kpi.sinking_cash_coverage * 100, 800)} max={800} colorClass={ss.bar}/>
                    <p className="text-xs text-slate-600">{formatCurrency(kpi.sinking_fund_balance)} vs {formatCurrency(kpi.sinking_quarter_budget)} Q-equiv</p>
                    <span
                        className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-black ${ss.badge}`}>{ss.label}</span>
                </div>
            </div>

            {/* ── Supporting Metrics Row ── */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {[
                    {
                        label: "Current Quarter Unpaid",
                        value: formatCurrency(kpi.current_quarter_unpaid_total),
                        tip: "The portion of this quarter's levy still unpaid across all lots.",
                        color: "text-amber-600"
                    },
                    {
                        label: "True Arrears",
                        value: formatCurrency(kpi.true_arrears_total),
                        tip: "Older arrears carried forward from prior quarters, after removing the current quarter levy component.",
                        color: "text-rose-600"
                    },
                    {
                        label: "Credits",
                        value: formatCurrency(kpi.credit_total),
                        tip: "Total overpayments and advance levy payments across all lots.",
                        color: "text-violet-600"
                    },
                    {
                        label: "Overall Liquidity",
                        value: `${(kpi.overall_liquidity_cash_only * 100).toFixed(1)}%`,
                        tip: `Total cash vs Q-billed. ${formatCurrency(kpi.total_cash_balance)} cash.`,
                        color: "text-indigo-600"
                    },
                ].map((m) => (
                    <div key={m.label} className="p-4 rounded-2xl bg-white border border-slate-100 shadow-sm">
                        <KpiLabel label={m.label} tip={m.tip}/>
                        <p className={`text-xl font-black mt-1 ${m.color}`}>{m.value}</p>
                    </div>
                ))}
            </div>

            {/* ── Charts ── */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Collections Waterfall */}
                <div className="lg:col-span-2 p-6 rounded-2xl bg-white border border-slate-100 shadow-sm">
                    <h3 className="font-black text-slate-800 mb-4">Collections Breakdown</h3>
                    <ResponsiveContainer width="100%" height={220}>
                        <BarChart data={waterfallData} margin={{top: 8, right: 8, left: 8, bottom: 4}}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9"/>
                            <XAxis dataKey="name" tick={{fontSize: 10, fontWeight: 700}}/>
                            <YAxis tickFormatter={(v) => formatMoneyCompact(v)} tick={{fontSize: 10}}/>
                            <RechartsTooltip formatter={(v: number) => formatCurrency(v)}/>
                            <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                                {waterfallData.map((d, i) => (
                                    <Cell key={i} fill={d.fill}/>
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </div>

                {/* Lot Status Donut */}
                <div className="p-6 rounded-2xl bg-white border border-slate-100 shadow-sm flex flex-col">
                    <h3 className="font-black text-slate-800 mb-4">Lot Payment Status</h3>
                    <div className="flex-1 flex items-center justify-center">
                        <ResponsiveContainer width="100%" height={180}>
                            <PieChart>
                                <Pie data={donutData} cx="50%" cy="50%" innerRadius={50} outerRadius={75}
                                     dataKey="value" paddingAngle={3}>
                                    {donutData.map((_, i) => (
                                        <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]}/>
                                    ))}
                                </Pie>
                                <RechartsTooltip/>
                            </PieChart>
                        </ResponsiveContainer>
                    </div>
                    <div className="grid grid-cols-3 gap-1 text-center text-[10px]">
                        {donutData.map((d, i) => (
                            <div key={d.name}>
                                <div className="w-2 h-2 rounded-full mx-auto mb-0.5"
                                     style={{background: PIE_COLORS[i % PIE_COLORS.length]}}/>
                                <p className="font-black text-slate-700">{d.value}</p>
                                <p className="text-slate-400">{d.name}</p>
                            </div>
                        ))}
                    </div>
                </div>
            </div>

            {/* ── Top True Arrears Table (management only) ── */}
            {isManagement && (kpi.top_true_arrears?.length ?? 0) > 0 && (
                <div className="p-6 rounded-2xl bg-white border border-slate-100 shadow-sm">
                    <h3 className="font-black text-slate-800 mb-4">Top True Arrears — Prior-Period Debt</h3>
                    <div className="overflow-x-auto">
                        <Table>
                            <TableHeader>
                                <TableRow className="text-[10px] uppercase tracking-widest">
                                    <TableHead>Lot</TableHead>
                                    <TableHead>Unit</TableHead>
                                    <TableHead>UOE</TableHead>
                                    <TableHead className="text-right">Current Balance</TableHead>
                                    <TableHead className="text-right">Q Levy</TableHead>
                                    <TableHead className="text-right">True Arrears</TableHead>
                                    <TableHead className="text-right">Q Unpaid</TableHead>
                                    <TableHead>Status</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {kpi.top_true_arrears.map((lot: any, i: number) => (
                                    <TableRow key={i} className="text-xs">
                                        <TableCell className="font-bold">{lot.lot}</TableCell>
                                        <TableCell>{lot.unit}</TableCell>
                                        <TableCell>{lot.uoe}</TableCell>
                                        <TableCell
                                            className="text-right font-bold text-rose-600">{formatCurrency(lot.current_balance)}</TableCell>
                                        <TableCell className="text-right">{formatCurrency(lot.quarter_levy)}</TableCell>
                                        <TableCell
                                            className="text-right font-black text-rose-700">{formatCurrency(lot.true_arrears)}</TableCell>
                                        <TableCell
                                            className="text-right text-amber-600 font-bold">{formatCurrency(lot.current_quarter_unpaid)}</TableCell>
                                        <TableCell>
                                            <span
                                                className={`px-2 py-0.5 rounded-full text-[9px] font-black ${lot.status === "arrears" ? "bg-rose-100 text-rose-700" : lot.status === "credit" ? "bg-violet-100 text-violet-700" : "bg-emerald-100 text-emerald-700"}`}>
                                                {lot.status}
                                            </span>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </div>
                </div>
            )}

            {/* ── Fund health detail ── */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Admin Fund */}
                <div className="p-6 rounded-2xl bg-white border border-slate-100 shadow-sm space-y-3">
                    <div className="flex justify-between items-start">
                        <div>
                            <p className="text-[10px] font-black text-slate-400 uppercase tracking-widest">Admin
                                Fund</p>
                            <p className={`text-2xl font-black mt-1 ${as_.color}`}>{(kpi.admin_fund_health * 100).toFixed(1)}%</p>
                            <p className="text-xs text-slate-500 mt-0.5">Cash: {formatCurrency(kpi.admin_fund_balance)} ·
                                Q-budget: {formatCurrency(kpi.admin_quarter_budget)}</p>
                        </div>
                        <span className={`px-3 py-1 rounded-full text-xs font-black ${as_.badge}`}>{as_.label}</span>
                    </div>
                    <GaugeBar value={kpi.admin_fund_health * 100} colorClass={as_.bar}/>
                    <p className="text-[10px] text-slate-400">Incl.
                        receivables: {(kpi.admin_fund_health_incl_receivables * 100).toFixed(1)}%</p>
                </div>

                {/* Sinking Fund */}
                <div className="p-6 rounded-2xl bg-white border border-slate-100 shadow-sm space-y-3">
                    <div className="flex justify-between items-start">
                        <div>
                            <p className="text-[10px] font-black text-slate-400 uppercase tracking-widest">Sinking
                                Fund</p>
                            <p className={`text-2xl font-black mt-1 ${ss.color}`}>{(kpi.sinking_cash_coverage * 100).toFixed(0)}%</p>
                            <p className="text-xs text-slate-500 mt-0.5">Cash: {formatCurrency(kpi.sinking_fund_balance)} ·
                                Q-equiv: {formatCurrency(kpi.sinking_quarter_budget)}</p>
                        </div>
                        <span className={`px-3 py-1 rounded-full text-xs font-black ${ss.badge}`}>{ss.label}</span>
                    </div>
                    <GaugeBar value={Math.min(kpi.sinking_cash_coverage * 100, 800)} max={800} colorClass={ss.bar}/>
                    <p className="text-[10px] text-slate-400">
                        Reserve
                        adequacy: {kpi.sinking_percent_funded != null ? `${(kpi.sinking_percent_funded * 100).toFixed(1)}%` : "Not yet available — reserve plan pending"}
                    </p>
                </div>
            </div>
        </div>
    );
}
