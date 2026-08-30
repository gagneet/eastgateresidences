// @ts-nocheck
"use client";
// @featuretrace:levy-kpi — Levy KPI Dialog popup for Collection Rate and Fund Health cards.
// Layer: frontend
// Data flow: (UNMOUNTED) → /finance/levy-kpi → unit_levy_ledger + annual_levies (building-scoped).
// Related: backend/routers/finance.py (get_levy_kpi), /financials/levy-kpi (full page)
//
// NOT RENDERED ANYWHERE (verified 2026-08-26). This header used to name
// ManagementDashboard and OwnerDashboard as the callers; neither imports it, and
// nothing else in src/ does either — the only importer is its own unit test. The
// live levy-KPI surface is the full page at /financials/levy-kpi.
//
// Kept rather than deleted because it is complete and tested, and wiring it to a
// dashboard is a product decision. But do not assume a change here reaches a user:
// it does not, until someone mounts it. It is deliberately excluded from
// tests/frontend/e2e/design-system-visual-verification.spec.js for the same reason —
// there is no route to screenshot it on.

import React, {useEffect, useState} from "react";
import {useRouter} from "next/navigation";
import {useAuth} from "@/contexts/AuthContext";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {Button} from "@/components/ui/button";
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
    ResponsiveContainer,
    Tooltip as RechartsTooltip,
    Bar,
    BarChart,
    XAxis,
    YAxis,
    CartesianGrid,
} from "recharts";
import {
    Activity,
    AlertCircle,
    ArrowRight,
    CheckCircle2,
    Coins,
    Info,
    PiggyBank,
    TrendingUp,
    Wallet,
} from "lucide-react";
import {
    Tooltip,
    TooltipContent,
    TooltipTrigger,
} from "@/components/ui/tooltip";
import {formatCurrency} from "@/lib/utils";
import {StatTile, type StatTone} from "@/components/shared/StatTile";
import {formatMoneyCompact} from '@/lib/currency';
import {
    CHART_STATUS,
    axisProps,
    barRadius,
    gridProps,
    seriesColor,
    tooltipProps,
} from "@/lib/chartTheme";
// ── Threshold helpers ────────────────────────────────────────────────────────

/**
 * Each threshold helper returns a StatTile `tone` plus the word for that band.
 *
 * They used to return four raw palette class strings each (`text-`, `bg-`,
 * `border-`, `badge`), which is how this dialog ended up with its own private
 * colour system. The tone is the design system's own good/warning/critical
 * vocabulary, and the label is what makes the band survive greyscale — colour is
 * never the only signal.
 */
type Rating = { tone: StatTone; label: string };

/** Due-date collection rate against the current quarter's billed levy. */
const collectionRating = (rate: number): Rating => {
    const pct = rate * 100;
    if (pct >= 95) return {tone: "good", label: "On Target"};
    if (pct >= 85) return {tone: "warning", label: "Slightly Below"};
    return {tone: "critical", label: "At Risk"};
};

/** Admin fund cash against next quarter's admin budget. */
const adminHealthRating = (rate: number): Rating => {
    const pct = rate * 100;
    if (pct >= 100) return {tone: "good", label: "Healthy"};
    if (pct >= 50) return {tone: "warning", label: "Watch"};
    return {tone: "critical", label: "At Risk"};
};

/** Sinking fund cash against the quarter-equivalent contribution (liquidity, not adequacy). */
const sinkingRating = (rate: number): Rating => {
    const pct = rate * 100;
    if (pct >= 200) return {tone: "good", label: "Liquid"};
    if (pct >= 100) return {tone: "warning", label: "Adequate"};
    return {tone: "critical", label: "Low"};
};

/** Share of lots fully paid for the quarter or in credit. */
const complianceRating = (rate: number): Rating => {
    const pct = rate * 100;
    if (pct >= 90) return {tone: "good", label: "Strong"};
    if (pct >= 75) return {tone: "warning", label: "Moderate"};
    return {tone: "critical", label: "Low"};
};

/** Gauge fill per tone. Chart colours come from chartTheme, never a local hex. */
const TONE_FILL: Record<StatTone, string> = {
    default: "hsl(var(--primary))",
    good: CHART_STATUS.good,
    warning: CHART_STATUS.warning,
    critical: CHART_STATUS.critical,
};

/** Band pill per tone — the same pairing StatTile uses for its icon chip. */
const TONE_PILL: Record<StatTone, string> = {
    default: "bg-muted text-muted-foreground",
    good: "bg-emerald-50 text-emerald-700",
    warning: "bg-amber-50 text-amber-700",
    critical: "bg-red-50 text-red-700",
};

// ── KPI Tooltip wrapper ──────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: KpiLabel
 * Path: frontend/src/components/finance/LevyKpiDialog.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const KpiLabel = ({label, tip}: { label: string; tip: string }) => (
    <Tooltip>
        <TooltipTrigger asChild>
            <span className="flex items-center gap-1 cursor-help">
                {label}
                <Info size={11} className="text-muted-foreground"/>
            </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs text-xs">{tip}</TooltipContent>
    </Tooltip>
);
// ── Gauge bar ─────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: GaugeBar
 * Path: frontend/src/components/finance/LevyKpiDialog.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const GaugeBar = ({value, max = 100, tone = "default"}: { value: number; max?: number; tone?: StatTone }) => (
    // Spans, not divs: this renders inside StatTile's `hint`, which is a <p>. A <div>
    // there is invalid nesting and React will warn on hydration.
    <span className="mt-2 block h-2 overflow-hidden rounded-full bg-muted">
        <span
            className="block h-full rounded-full transition-all duration-700"
            style={{width: `${Math.min(100, (value / max) * 100)}%`, backgroundColor: TONE_FILL[tone]}}
        />
    </span>
);

/** Band pill shown under a KPI figure. Always paired with the figure's own caption. */
const BandPill = ({rating}: { rating: Rating }) => (
    <span className={`mt-1 inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold ${TONE_PILL[rating.tone]}`}>
        {rating.label}
    </span>
);

// ── Tab identifiers ───────────────────────────────────────────────────────────

type Tab = "collection" | "funds";

// ── Props ─────────────────────────────────────────────────────────────────────

interface LevyKpiDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    selectedYear?: string;
    initialTab?: Tab;
}
// ── Main component ────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: LevyKpiDialog
 * Path: frontend/src/components/finance/LevyKpiDialog.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function LevyKpiDialog({
                                          open,
                                          onOpenChange,
                                          selectedYear,
                                          initialTab = "collection",
                                      }: LevyKpiDialogProps) {
    const {api} = useAuth();
    const router = useRouter();
    const [tab, setTab] = useState<Tab>(initialTab);
    const [kpi, setKpi] = useState<any | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Gate per-lot sensitive data: rely on the backend's can_view_top_true_arrears flag.
    // The backend already redacts top_true_arrears to [] for non-privileged users, so
    // this flag is only used for UI rendering decisions (not a security boundary).
    const canViewTopArrears = kpi?.can_view_top_true_arrears === true;

    useEffect(() => {
        setTab(initialTab);
    }, [initialTab]);

    useEffect(() => {
        if (!open) return;
        setLoading(true);
        setError(null);
        const params = selectedYear ? `?year=${selectedYear}` : "";
        api
            .get(`/finance/levy-kpi${params}`)
            .then((res: any) => setKpi(res.data))
            .catch(() => setError("Could not load KPI data"))
            .finally(() => setLoading(false));
    }, [open, selectedYear, api]);

    // ── Derived display values ────────────────────────────────────────────────

    const collectionPct = kpi ? Math.round(kpi.collection_rate * 100 * 100) / 100 : 0;
    const compliancePct = kpi ? Math.round(kpi.lot_compliance_rate * 100 * 100) / 100 : 0;
    const adminHealthPct = kpi ? Math.round(kpi.admin_fund_health * 100 * 100) / 100 : 0;
    const adminHealthInclPct = kpi ? Math.round(kpi.admin_fund_health_incl_receivables * 100 * 100) / 100 : 0;
    const sinkingPct = kpi ? Math.round(kpi.sinking_cash_coverage * 100 * 100) / 100 : 0;
    const liquidityPct = kpi ? Math.round(kpi.overall_liquidity_cash_only * 100 * 100) / 100 : 0;
    const liquidityInclPct = kpi ? Math.round(kpi.overall_liquidity_incl_receivables * 100 * 100) / 100 : 0;

    const cRating = collectionRating(kpi?.collection_rate ?? 0);
    const aRating = adminHealthRating(kpi?.admin_fund_health ?? 0);
    const sRating = sinkingRating(kpi?.sinking_cash_coverage ?? 0);
    const compRating = complianceRating(kpi?.lot_compliance_rate ?? 0);

    // Lot status donut. Compliance is a STATE, so both slices come from CHART_STATUS.
    const donutData = kpi
        ? [
            {name: "Paid / Credit", value: kpi.compliant_lot_count, fill: CHART_STATUS.good},
            {name: "In Arrears", value: kpi.non_compliant_lot_count, fill: CHART_STATUS.critical},
        ]
        : [];

    // Collections breakdown. "Billed" and "Credits" are neutral quantities and take
    // categorical slots; the three middle bars encode collection state and take
    // CHART_STATUS, so the chart reads the same way as the KPI tiles above it.
    const waterfallData = kpi
        ? [
            {name: "Billed", value: kpi.quarter_billed_total_display, fill: seriesColor(0)},
            {name: "Collected", value: kpi.current_quarter_collected_total, fill: CHART_STATUS.good},
            {name: "Unpaid Q", value: kpi.current_quarter_unpaid_total, fill: CHART_STATUS.warning},
            {name: "True Arrears", value: kpi.true_arrears_total, fill: CHART_STATUS.critical},
            {name: "Credits", value: kpi.credit_total, fill: seriesColor(2)},
        ]
        : [];

    // ── Render ────────────────────────────────────────────────────────────────

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-4xl p-0 overflow-hidden max-h-[92vh] flex flex-col">
                {/* Header */}
                {/* Inverted hero, restored as a deliberate emphasis device.
                    The first migration flattened this to a plain bordered strip because the
                    original was a raw indigo-to-slate band, and a blanket hue swap would
                    have produced a degenerate one-colour gradient. Flat was the safe fix,
                    not the right one. (Naming the old classes here would trip the contract
                    test, which reads the raw file — describe them, do not reproduce them.)
                    It is now built from the brand tokens: bg-primary as the base with a
                    gradient travelling 30% toward the secondary terracotta. That ceiling is
                    measured, not chosen — white text sits between 5.02:1 and 6.66:1 across
                    the whole ramp, where a full from-primary/to-secondary would have ended
                    at 2.48:1 and been worse than what it replaced. */}
                <div className="bg-primary bg-gradient-to-br from-primary via-primary to-secondary/30 px-7 pt-6 pb-5 text-primary-foreground shrink-0">
                    <DialogHeader>
                        <DialogTitle className="text-xl font-semibold text-primary-foreground">Levy KPI Dashboard</DialogTitle>
                        <DialogDescription className="text-sm text-primary-foreground/70">
                            Quarter-level collection and fund health metrics — FY {kpi?.year || selectedYear || "…"}
                        </DialogDescription>
                    </DialogHeader>
                    {/* Tabs */}
                    <div className="flex gap-2 mt-4">
                        {(["collection", "funds"] as Tab[]).map((t) => (
                            <button
                                key={t}
                                type="button"
                                aria-pressed={tab === t}
                                onClick={() => setTab(t)}
                                className={`rounded-full px-4 py-1.5 text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                                    tab === t
                                        ? "bg-primary-foreground text-primary"
                                        : "bg-primary-foreground/15 text-primary-foreground hover:bg-primary-foreground/25"
                                }`}
                            >
                                {t === "collection" ? "Collections" : "Fund Health"}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Body */}
                <div className="overflow-auto flex-1 p-7 space-y-6 bg-card">
                    {loading && (
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            {[...Array(4)].map((_, i) => (
                                <div key={i} className="h-28 bg-muted animate-pulse rounded-xl"/>
                            ))}
                        </div>
                    )}

                    {error && (
                        <div
                            className="p-4 rounded-xl border border-rose-200 bg-rose-50 text-rose-700 text-sm flex items-center gap-2">
                            <AlertCircle size={16}/>
                            {error}
                        </div>
                    )}

                    {kpi && !loading && tab === "collection" && (
                        <>
                            {/* ── Collection KPI cards ── */}
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                <StatTile
                                    label={<KpiLabel label="Collection Rate"
                                                     tip="Percentage of the current quarter levy that has been covered, excluding prior-period arrears."/>}
                                    icon={<TrendingUp size={18}/>}
                                    value={`${collectionPct}%`}
                                    tone={cRating.tone}
                                    hint={
                                        <>
                                            <GaugeBar value={collectionPct} tone={cRating.tone}/>
                                            <span className="mt-1.5 block">
                                                {formatCurrency(kpi.current_quarter_collected_total)} of {formatCurrency(kpi.quarter_billed_total_display)}
                                            </span>
                                            <BandPill rating={cRating}/>
                                        </>
                                    }
                                />

                                <StatTile
                                    label={<KpiLabel label="Lot Compliance"
                                                     tip="Lots that are either fully paid for the quarter or currently in credit."/>}
                                    icon={<CheckCircle2 size={18}/>}
                                    value={`${compliancePct}%`}
                                    tone={compRating.tone}
                                    hint={
                                        <>
                                            <GaugeBar value={compliancePct} tone={compRating.tone}/>
                                            <span className="mt-1.5 block">
                                                {kpi.compliant_lot_count} of {kpi.total_lot_count} lots paid or in credit
                                            </span>
                                            <BandPill rating={compRating}/>
                                        </>
                                    }
                                />

                                <StatTile
                                    label={<KpiLabel label="True Arrears"
                                                     tip="Older arrears carried forward from prior quarter(s), after removing the current quarter levy component."/>}
                                    icon={<AlertCircle size={18}/>}
                                    value={formatCurrency(kpi.true_arrears_total)}
                                    tone={kpi.true_arrears_total > 0 ? "critical" : "good"}
                                    hint={
                                        <>
                                            <span className="mt-1.5 block">
                                                {kpi.prior_period_arrears_rate != null
                                                    ? `${(kpi.prior_period_arrears_rate * 100).toFixed(2)}% of quarter billed`
                                                    : "Share of quarter billed — not available"}
                                            </span>
                                            <span className="block">Older debt outstanding</span>
                                        </>
                                    }
                                />

                                <StatTile
                                    label="Credits"
                                    icon={<Coins size={18}/>}
                                    value={formatCurrency(kpi.credit_total)}
                                    hint={
                                        <>
                                            <span className="block">Current quarter unpaid:</span>
                                            <span className="block font-semibold text-amber-700">
                                                {formatCurrency(kpi.current_quarter_unpaid_total)}
                                            </span>
                                        </>
                                    }
                                />
                            </div>

                            {/* ── Charts row ── */}
                            <div className="grid grid-cols-1 md:grid-cols-5 gap-6">
                                {/* Waterfall */}
                                <div className="md:col-span-3">
                                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-3">Collections
                                        Breakdown</p>
                                    <ResponsiveContainer width="100%" height={180}>
                                        <BarChart data={waterfallData} margin={{top: 4, right: 4, left: 0, bottom: 4}}>
                                            <CartesianGrid {...gridProps}/>
                                            <XAxis dataKey="name" {...axisProps}/>
                                            <YAxis {...axisProps} tickFormatter={(v) => formatMoneyCompact(v)}/>
                                            <RechartsTooltip {...tooltipProps} formatter={(v: any) => formatCurrency(v)}/>
                                            <Bar dataKey="value" radius={barRadius}>
                                                {waterfallData.map((entry, i) => (
                                                    <Cell key={i} fill={entry.fill}/>
                                                ))}
                                            </Bar>
                                        </BarChart>
                                    </ResponsiveContainer>
                                </div>

                                {/* Donut */}
                                <div className="md:col-span-2">
                                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-3">Lot
                                        Status</p>
                                    <ResponsiveContainer width="100%" height={180}>
                                        <PieChart>
                                            <Pie
                                                data={donutData}
                                                cx="50%"
                                                cy="50%"
                                                innerRadius={52}
                                                outerRadius={75}
                                                paddingAngle={3}
                                                dataKey="value"
                                            >
                                                {donutData.map((entry, i) => (
                                                    <Cell key={i} fill={entry.fill}/>
                                                ))}
                                            </Pie>
                                            <RechartsTooltip {...tooltipProps}/>
                                        </PieChart>
                                    </ResponsiveContainer>
                                    <div className="flex justify-center gap-4 -mt-2">
                                        {donutData.map((d, i) => (
                                            <div key={i} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                                                <span className="w-2.5 h-2.5 rounded-full"
                                                      style={{background: d.fill}}/>
                                                {d.name}: {d.value}
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            </div>

                            {/* ── Top True Arrears table — privileged users only ── */}
                            {canViewTopArrears && kpi.top_true_arrears?.length > 0 && (
                                <div>
                                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-3">Top
                                        True Arrears Lots</p>
                                    <div className="border rounded-xl overflow-hidden">
                                        <Table>
                                            <TableHeader>
                                                <TableRow className="bg-muted">
                                                    <TableHead
                                                        className="text-xs font-semibold uppercase tracking-widest">Lot</TableHead>
                                                    <TableHead
                                                        className="text-xs font-semibold uppercase tracking-widest">Unit</TableHead>
                                                    <TableHead
                                                        className="text-xs font-semibold uppercase tracking-widest text-right">UOE</TableHead>
                                                    <TableHead
                                                        className="text-xs font-semibold uppercase tracking-widest text-right">Balance</TableHead>
                                                    <TableHead
                                                        className="text-xs font-semibold uppercase tracking-widest text-right">True
                                                        Arrears</TableHead>
                                                    <TableHead
                                                        className="text-xs font-semibold uppercase tracking-widest text-right">Q
                                                        Unpaid</TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {kpi.top_true_arrears.slice(0, 10).map((lot: any, i: number) => (
                                                    <TableRow key={i} className="hover:bg-muted/50">
                                                        <TableCell
                                                            className="font-bold text-sm">{lot.lot || "—"}</TableCell>
                                                        <TableCell
                                                            className="text-sm text-muted-foreground">{lot.unit}</TableCell>
                                                        <TableCell
                                                            className="text-right text-sm font-mono">{lot.uoe}</TableCell>
                                                        <TableCell
                                                            className="text-right text-sm font-mono text-rose-600">{formatCurrency(lot.current_balance)}</TableCell>
                                                        <TableCell
                                                            className="text-right text-sm font-mono font-bold text-rose-700">{formatCurrency(lot.true_arrears)}</TableCell>
                                                        <TableCell
                                                            className="text-right text-sm font-mono text-amber-600">{formatCurrency(lot.current_quarter_unpaid)}</TableCell>
                                                    </TableRow>
                                                ))}
                                            </TableBody>
                                        </Table>
                                    </div>
                                </div>
                            )}

                            {/* Rounding note */}
                            {kpi.quarter_billed_total_display !== kpi.quarter_billed_total_lot_sum && (
                                <p className="text-[10px] text-muted-foreground">
                                    ℹ Display total ({formatCurrency(kpi.quarter_billed_total_display)}) differs from
                                    lot-sum ({formatCurrency(kpi.quarter_billed_total_lot_sum)}) by rounding — headline
                                    uses the canonical annual÷4 figure.
                                </p>
                            )}
                        </>
                    )}

                    {kpi && !loading && tab === "funds" && (
                        <>
                            {/* ── Fund Health cards ── */}
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                <StatTile
                                    label={<KpiLabel label="Admin Fund Health"
                                                     tip="Current administrative fund cash as a percentage of the next quarter's administrative budget."/>}
                                    icon={<Wallet size={18}/>}
                                    value={`${adminHealthPct}%`}
                                    tone={aRating.tone}
                                    hint={
                                        <>
                                            <GaugeBar value={adminHealthPct} tone={aRating.tone}/>
                                            <span className="mt-1.5 block">
                                                {formatCurrency(kpi.admin_fund_balance)} cash vs {formatCurrency(kpi.admin_quarter_budget)} next-Q need
                                            </span>
                                            <span className="block">
                                                Incl. receivables: <span className="font-semibold">{adminHealthInclPct}%</span>
                                            </span>
                                            <BandPill rating={aRating}/>
                                        </>
                                    }
                                />

                                <StatTile
                                    label={<KpiLabel label="Sinking Coverage"
                                                     tip="Current sinking fund cash as a percentage of the next quarter equivalent sinking contribution. This is a liquidity measure, not full reserve adequacy."/>}
                                    icon={<PiggyBank size={18}/>}
                                    value={`${sinkingPct}%`}
                                    tone={sRating.tone}
                                    hint={
                                        <>
                                            <GaugeBar value={Math.min(sinkingPct, 800)} max={800} tone={sRating.tone}/>
                                            <span className="mt-1.5 block">
                                                {formatCurrency(kpi.sinking_fund_balance)} cash vs {formatCurrency(kpi.sinking_quarter_budget)} Q-equivalent
                                            </span>
                                            <span className="block">
                                                Reserve adequacy: {kpi.sinking_percent_funded != null
                                                ? `${(kpi.sinking_percent_funded * 100).toFixed(1)}%`
                                                : "Not yet available"}
                                            </span>
                                            <BandPill rating={sRating}/>
                                        </>
                                    }
                                />

                                <StatTile
                                    label="Overall Liquidity"
                                    icon={<Activity size={18}/>}
                                    value={`${liquidityPct}%`}
                                    hint={
                                        <>
                                            <GaugeBar value={Math.min(liquidityPct, 300)} max={300}/>
                                            <span className="mt-1.5 block">
                                                {formatCurrency(kpi.total_cash_balance)} total cash
                                            </span>
                                            <span className="block">
                                                Incl. receivables: <span className="font-semibold">{liquidityInclPct}%</span>
                                            </span>
                                        </>
                                    }
                                />
                            </div>

                            {/* ── Current Balance Breakdown ── */}
                            <div className="rounded-xl border border-border bg-card overflow-hidden">
                                <div
                                    className="px-5 py-3 bg-muted border-b border-border flex items-center justify-between">
                                    <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
                                        <KpiLabel
                                            label="Current Balance"
                                            tip="Computed from database transactions (opening + YTD levy income − YTD expenses). Cross-check shows the Strata Mgmt system bank balance for reconciliation."
                                        />
                                    </p>
                                    <p className="text-xl font-semibold text-foreground tabular-nums">{formatCurrency(kpi.total_live_balance)}</p>
                                </div>
                                <div
                                    className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-border">
                                    {/* Admin fund */}
                                    <div className="p-5 space-y-2">
                                        <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-3">Admin
                                            Fund — <span
                                                className="text-foreground">{formatCurrency(kpi.admin_live_balance)}</span>
                                        </p>
                                        <div className="flex justify-between text-sm">
                                            <span className="text-muted-foreground">FY Opening Balance</span>
                                            <span
                                                className="font-bold text-foreground">{formatCurrency(kpi.admin_opening_balance)}</span>
                                        </div>
                                        <div className="flex justify-between text-sm">
                                            <span
                                                className="text-muted-foreground">+ YTD Levy Income ({kpi.admin_ratio != null ? ((kpi.admin_ratio) * 100).toFixed(1) : "—"}% share)</span>
                                            <span
                                                className="font-bold text-emerald-600">+{formatCurrency(kpi.ytd_admin_paid)}</span>
                                        </div>
                                        <div className="flex justify-between text-sm">
                                            <span className="text-muted-foreground">− YTD Expenses</span>
                                            <span
                                                className="font-bold text-rose-600">−{formatCurrency(kpi.ytd_admin_expenses)}</span>
                                        </div>
                                        <div
                                            className="flex justify-between text-xs border-t border-border pt-2 mt-1 text-muted-foreground">
                                            <span>Strata Mgmt (cross-check)</span>
                                            <span
                                                className="font-bold">{formatCurrency(kpi.strata_mgmt_admin_balance)}</span>
                                        </div>
                                    </div>
                                    {/* Sinking fund */}
                                    <div className="p-5 space-y-2">
                                        <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-3">Sinking
                                            Fund — <span
                                                className="text-foreground">{formatCurrency(kpi.sinking_live_balance)}</span>
                                        </p>
                                        <div className="flex justify-between text-sm">
                                            <span className="text-muted-foreground">FY Opening Balance</span>
                                            <span
                                                className="font-bold text-foreground">{formatCurrency(kpi.sinking_opening_balance)}</span>
                                        </div>
                                        <div className="flex justify-between text-sm">
                                            <span
                                                className="text-muted-foreground">+ YTD Levy Income ({kpi.admin_ratio != null ? (100 - (kpi.admin_ratio) * 100).toFixed(1) : "—"}% share)</span>
                                            <span
                                                className="font-bold text-emerald-600">+{formatCurrency(kpi.ytd_sinking_paid)}</span>
                                        </div>
                                        <div className="flex justify-between text-sm">
                                            <span className="text-muted-foreground">− YTD Expenses</span>
                                            <span
                                                className="font-bold text-rose-600">−{formatCurrency(kpi.ytd_sinking_expenses)}</span>
                                        </div>
                                        <div
                                            className="flex justify-between text-xs border-t border-border pt-2 mt-1 text-muted-foreground">
                                            <span>Strata Mgmt (cross-check)</span>
                                            <span
                                                className="font-bold">{formatCurrency(kpi.strata_mgmt_sinking_balance)}</span>
                                        </div>
                                    </div>
                                </div>
                                <div
                                    className="px-5 py-2 bg-muted/60 border-t border-border flex flex-wrap items-center justify-between gap-2">
                                    <p className="text-[10px] text-muted-foreground">
                                        Computed from database transactions ·
                                        Strata Mgmt system balance: <span
                                        className="font-bold">{formatCurrency(kpi.strata_mgmt_total_balance)}</span> ·
                                        Gap: <span
                                        className="font-bold">{formatCurrency(Math.abs(kpi.total_live_balance - (kpi.strata_mgmt_total_balance ?? 0)))}</span> (est.
                                        interest income)
                                    </p>
                                </div>
                            </div>

                            {/* ── Fund balance breakdown ── */}
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div className="p-5 rounded-xl border border-border bg-muted/60 space-y-3">
                                    <div className="flex items-center justify-between mb-1">
                                        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Admin
                                            Fund</p>
                                        <span className="text-[10px] text-muted-foreground italic">Bank-reconciled</span>
                                    </div>
                                    <div className="grid grid-cols-2 gap-2 text-sm">
                                        <div><p className="text-muted-foreground text-[10px]">Strata Mgmt Balance</p><p
                                            className="font-semibold text-foreground tabular-nums">{formatCurrency(kpi.admin_fund_balance)}</p>
                                        </div>
                                        <div><p className="text-muted-foreground text-[10px]">Q Budget Need</p><p
                                            className="font-semibold text-foreground">{formatCurrency(kpi.admin_quarter_budget)}</p>
                                        </div>
                                        <div><p className="text-muted-foreground text-[10px]">Annual Gross</p><p
                                            className="font-semibold text-foreground">{formatCurrency(kpi.admin_annual_gross)}</p>
                                        </div>
                                        <div><p className="text-muted-foreground text-[10px]">Admin Share</p><p
                                            className="font-semibold text-foreground">{kpi.total_annual_gross > 0 ? ((kpi.admin_annual_gross / kpi.total_annual_gross) * 100).toFixed(1) : 0}%</p>
                                        </div>
                                    </div>
                                </div>
                                <div className="p-5 rounded-xl border border-border bg-muted/60 space-y-3">
                                    <div className="flex items-center justify-between mb-1">
                                        <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Sinking
                                            Fund</p>
                                        <span className="text-[10px] text-muted-foreground italic">Bank-reconciled</span>
                                    </div>
                                    <div className="grid grid-cols-2 gap-2 text-sm">
                                        <div><p className="text-muted-foreground text-[10px]">Strata Mgmt Balance</p><p
                                            className="font-semibold text-foreground tabular-nums">{formatCurrency(kpi.sinking_fund_balance)}</p>
                                        </div>
                                        <div><p className="text-muted-foreground text-[10px]">Q Budget Need</p><p
                                            className="font-semibold text-foreground">{formatCurrency(kpi.sinking_quarter_budget)}</p>
                                        </div>
                                        <div><p className="text-muted-foreground text-[10px]">Annual Gross</p><p
                                            className="font-semibold text-foreground">{formatCurrency(kpi.sinking_annual_gross)}</p>
                                        </div>
                                        <div><p className="text-muted-foreground text-[10px]">Sinking Share</p><p
                                            className="font-semibold text-foreground">{kpi.total_annual_gross > 0 ? ((kpi.sinking_annual_gross / kpi.total_annual_gross) * 100).toFixed(1) : 0}%</p>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* ── Net arrears outstanding ── */}
                            <div
                                className="p-4 rounded-xl border border-amber-200 bg-amber-50 flex flex-wrap items-center justify-between gap-4">
                                <div>
                                    <p className="text-[10px] font-semibold uppercase tracking-widest text-amber-600 mb-0.5">Net
                                        Arrears Outstanding</p>
                                    <p className="text-2xl font-semibold text-amber-700">{formatCurrency(kpi.net_arrears_outstanding)}</p>
                                    <p className="text-[10px] text-muted-foreground mt-0.5">Total
                                        arrears {formatCurrency(kpi.arrears_total)} minus
                                        credits {formatCurrency(kpi.credit_total)}</p>
                                </div>
                                <div className="text-right">
                                    <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-0.5">Net
                                        Cash Collected (Q)</p>
                                    <p className="text-xl font-semibold text-foreground">{formatCurrency(kpi.net_cash_collected_total)}</p>
                                </div>
                            </div>
                        </>
                    )}
                </div>

                {/* Footer */}
                <div className="p-5 bg-muted/60 border-t border-border flex flex-wrap justify-between items-center gap-3 shrink-0">
                    <p className="text-[10px] text-muted-foreground">
                        Data: unit_levy_ledger + annual_levies · FY {kpi?.year || selectedYear} ·
                        Building {kpi?.building_id}
                    </p>
                    <div className="flex gap-3">
                        <Button variant="outline" onClick={() => onOpenChange(false)}>
                            Close
                        </Button>
                        <Button
                            onClick={() => {
                                onOpenChange(false);
                                router.push(`/financials/levy-kpi${selectedYear ? `?year=${selectedYear}` : ""}`);
                            }}
                            className="gap-2"
                        >
                            Full Analysis <ArrowRight size={14}/>
                        </Button>
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    );
}
