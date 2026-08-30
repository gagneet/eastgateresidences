// @featuretrace:bi-analytics — BI Analytics page: 14-panel canonical data intelligence workspace.
// Layer: frontend
// Data flow: BIAnalyticsPage → GET /api/bi/building/{id}/* → bi_service → analytics.fact_* (PG) or Mongo fallback.
// Related: backend/routers/bi.py
//           backend/services/bi_service.py
//           backend/alembic/versions/0052_canonical_bi_schema.py
// Toggle: bi_analytics_enabled
// Tests: tests/frontend/unit/pages/dashboard/BIAnalyticsPage.test.tsx
"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
    Activity,
    AlertTriangle,
    BarChart3,
    Building2,
    CheckCircle2,
    ChevronRight,
    Database,
    HardDriveDownload,
    Layers,
    RefreshCw,
    ShieldCheck,
    TrendingDown,
    TrendingUp,
    Wrench,
    Zap,
} from "lucide-react";
import {
    Area,
    AreaChart,
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    Line,
    LineChart,
    Pie,
    PieChart,
    ResponsiveContainer,
    Tooltip as RechartsTooltip,
    XAxis,
    YAxis,
} from "recharts";

import { useAuth } from "@/contexts/AuthContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PageHeader } from "@/components/shared/PageHeader";
import { CHART_SERIES, CHART_STATUS, CHART_INK, axisProps, gridProps, tooltipProps, barRadius, sequentialStep } from "@/lib/chartTheme";
import { formatCurrency } from "@/lib/utils";

// ─── Constants ──────────────────────────────────────────────────────────────

const ALLOWED_ROLES = ["super_admin", "strata_manager", "strata_admin", "ec_member"];

const RAG_COLORS: Record<string, string> = {
    green: CHART_STATUS.good,
    amber: CHART_STATUS.warning,
    red: CHART_STATUS.critical,
};

const CHART_COLORS = CHART_SERIES;
// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: pct
 * Path: frontend/src/pages/dashboard/BIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const pct = (v?: number | null, digits = 1) =>
    typeof v === "number" && !Number.isNaN(v) ? `${v.toFixed(digits)}%` : "—";
/**
 * @generated FunctionHeader
 * Function: fmtK
 * Path: frontend/src/pages/dashboard/BIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmtK = (v?: number | null) =>
    typeof v === "number" ? (v >= 1000 ? `$${(v / 1000).toFixed(0)}k` : formatCurrency(v)) : "—";
/**
 * @generated FunctionHeader
 * Function: ragBadge
 * Path: frontend/src/pages/dashboard/BIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ragBadge = (status?: string) => {
    const color = status === "green" ? "bg-emerald-100 text-emerald-800"
        : status === "amber" ? "bg-amber-100 text-amber-800"
        : status === "red" ? "bg-red-100 text-red-800"
        : "bg-muted text-muted-foreground";
    return <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${color}`}>{status || "—"}</span>;
};
// ─── Panel primitives ────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: PanelShell
 * Path: frontend/src/pages/dashboard/BIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function PanelShell({
    title,
    description,
    drilldown,
    loading,
    error,
    empty,
    badge,
    children,
    className = "",
}: {
    title: string;
    description?: string;
    drilldown?: string;
    loading?: boolean;
    error?: string | null;
    empty?: boolean;
    badge?: React.ReactNode;
    children: React.ReactNode;
    className?: string;
}) {
    const router = useRouter();
    return (
        <Card className={className}>
            <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                    <div>
                        <CardTitle className="text-base font-semibold text-foreground">{title}</CardTitle>
                        {description && <CardDescription className="mt-0.5 text-xs">{description}</CardDescription>}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                        {badge}
                        {drilldown && (
                            <button
                                onClick={() => router.push(drilldown)}
                                className="rounded-full p-1 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                                title="View detail"
                            >
                                <ChevronRight className="h-4 w-4" />
                            </button>
                        )}
                    </div>
                </div>
            </CardHeader>
            <CardContent>
                {loading ? (
                    <div className="flex h-32 items-center justify-center">
                        <RefreshCw className="h-5 w-5 animate-spin text-muted-foreground" aria-label="Loading" />
                    </div>
                ) : error ? (
                    <div role="alert" className="flex h-32 items-center justify-center rounded-xl bg-red-50 text-sm text-red-700">
                        <AlertTriangle className="mr-2 h-4 w-4" /> {error}
                    </div>
                ) : empty ? (
                    <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
                        No data available yet
                    </div>
                ) : (
                    children
                )}
            </CardContent>
        </Card>
    );
}
/**
 * @generated FunctionHeader
 * Function: KpiTile
 * Path: frontend/src/pages/dashboard/BIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function KpiTile({
    title,
    value,
    sub,
    icon: Icon,
    trend,
    color = "indigo",
    loading,
}: {
    title: string;
    value: React.ReactNode;
    sub?: string;
    icon: React.ElementType;
    trend?: "up" | "down" | null;
    color?: string;
    loading?: boolean;
}) {
    // Only the STATE-carrying tones survive as colour. `indigo`/`violet`/`blue`/
    // `cyan` were decorative differentiation between neutral metrics — that is
    // exactly the per-page invented palette this page was rewritten to remove, so
    // they collapse to the neutral chip. Callers may keep passing those names;
    // they resolve to `neutral` rather than breaking.
    const colorMap: Record<string, string> = {
        neutral: "bg-muted text-muted-foreground",
        emerald: "bg-emerald-50 text-emerald-700",
        amber: "bg-amber-50 text-amber-700",
        red: "bg-red-50 text-red-700",
    };
    return (
        <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
            <div className="flex items-start justify-between">
                <div className={`rounded-xl p-2.5 ${colorMap[color] || colorMap.neutral}`}>
                    <Icon className="h-4 w-4" />
                </div>
                {trend === "up" && <TrendingUp className="h-4 w-4 text-emerald-700" aria-label="Trending up" />}
                {trend === "down" && <TrendingDown className="h-4 w-4 text-red-700" aria-label="Trending down" />}
            </div>
            <div className="mt-3">
                {loading ? (
                    <div className="h-7 w-24 animate-pulse rounded bg-muted" aria-hidden="true" />
                ) : (
                    <p className="text-2xl font-semibold text-foreground">{value}</p>
                )}
                <p className="mt-1 text-sm text-muted-foreground">{title}</p>
                {sub && <p className="mt-1 text-xs text-muted-foreground">{sub}</p>}
            </div>
        </div>
    );
}
// ─── useBI hook — fetches one BI endpoint with loading/error state ────────────

/**
 * @generated FunctionHeader
 * Function: useBI
 * Path: frontend/src/pages/dashboard/BIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function useBI<T>(
    fetcher: (() => Promise<T>) | null,
    deps: React.DependencyList,
): { data: T | null; loading: boolean; error: string | null; refresh: () => void } {
    const [data, setData] = useState<T | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [tick, setTick] = useState(0);
    const refresh = useCallback(() => setTick((t) => t + 1), []);

    useEffect(() => {
        if (!fetcher) {
            setLoading(false);
            return;
        }
        let cancelled = false;
        setLoading(true);
        setError(null);
        fetcher()
            .then((result) => {
                if (!cancelled) {
                    setData(result);
                    setLoading(false);
                }
            })
            .catch((err: Error) => {
                if (!cancelled) {
                    setError(err?.message || "Failed to load");
                    setLoading(false);
                }
            });
        return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [...deps, tick]);

    return { data, loading, error, refresh };
}
// ─── Main page ────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: BIAnalyticsPage
 * Path: frontend/src/pages/dashboard/BIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function BIAnalyticsPage() {
    const {
        api,
        selectedBuilding,
        selectedYear,
        user,
        loading: authLoading,
        hasFeatureAccess,
        hasPermission,
    } = useAuth() as any;
    const router = useRouter();

    const bid = selectedBuilding?.building_id || selectedBuilding?.id || "";
    const fy = selectedYear || String(new Date().getFullYear());

    const effectiveRole = user?.effective_role || user?.role || "";
    const canViewPage = !!user && ALLOWED_ROLES.includes(effectiveRole) && hasPermission?.("can_view_finances");

    // Resolved BI access from backend (scope + toggle state)
    const { data: biAccess } = useBI<{
        enabled: boolean; scope: string; source: string;
        pg_primary: boolean; reason: string; warnings: string[];
    }>(
        bid && canViewPage
            ? () => api.get(`/bi/access?building_id=${bid}`).then((r: any) => r.data)
            : null,
        [bid, canViewPage],
    );

    const biEnabled = biAccess?.enabled ?? false;
    const pgPrimary = biAccess?.pg_primary ?? false;
    const biWarnings = biAccess?.warnings ?? [];
    // Feature toggle check — show page if either the resolved access says enabled
    // or the frontend feature flags indicate it should be visible
    const toggleOn = biEnabled || hasFeatureAccess?.("bi_analytics_enabled") ||
        hasFeatureAccess?.("bi_analytics_building_enabled");

    useEffect(() => {
        if (!authLoading && user && !canViewPage) router.replace("/dashboard");
    }, [authLoading, canViewPage, router, user]);

    // ── Fetch all BI panels ────────────────────────────────────────────────

    const { data: finSummary, loading: finLoading, error: finError } = useBI<{
        levy_collection_rate?: number;
        total_paid?: number;
        total_levied?: number;
        total_arrears?: number;
        lots_in_arrears?: number;
        admin_fund_balance?: number;
        sinking_fund_balance?: number;
    }>(
        bid ? () => api.get(`/bi/building/${bid}/financial-summary?financial_year=${fy}`).then((r: any) => r.data?.data) : null,
        [bid, fy],
    );

    const { data: levyTrend, loading: trendLoading, error: trendError } = useBI(
        bid ? () => api.get(`/bi/building/${bid}/levy-collection-trend?months=12`).then((r: any) => r.data?.data) : null,
        [bid],
    );

    const { data: arrears, loading: arrearsLoading, error: arrearsError } = useBI(
        bid ? () => api.get(`/bi/building/${bid}/arrears-hotspots?min_days_overdue=0`).then((r: any) => r.data?.data) : null,
        [bid],
    );

    const { data: capex, loading: capexLoading, error: capexError } = useBI(
        bid ? () => api.get(`/bi/building/${bid}/capex-vs-plan?years=10`).then((r: any) => r.data?.data) : null,
        [bid],
    );

    const { data: sfProjection, loading: sfLoading, error: sfError } = useBI<{
        projection?: Array<{ year?: string | number; date?: string; balance?: number }>;
        shortfall_risk?: boolean;
    }>(
        bid ? () => api.get(`/bi/building/${bid}/sinking-fund-projection?years=10`).then((r: any) => r.data?.data) : null,
        [bid],
    );

    const { data: healthTrend, loading: healthLoading, error: healthError } = useBI(
        bid ? () => api.get(`/bi/building/${bid}/health-trend?days=90`).then((r: any) => r.data?.data) : null,
        [bid],
    );

    const { data: maintCosts, loading: maintLoading, error: maintError } = useBI<{
        by_category?: Array<{ category?: string; total_cost?: number; work_orders?: number }>;
        total_work_orders?: number;
        total_cost?: number;
        open_work_orders?: number;
        sla_breaches?: number;
    }>(
        bid ? () => api.get(`/bi/building/${bid}/maintenance-costs?financial_year=${fy}`).then((r: any) => r.data?.data) : null,
        [bid, fy],
    );

    const { data: complianceStatus, loading: compLoading, error: compError } = useBI(
        bid ? () => api.get(`/bi/building/${bid}/compliance-status`).then((r: any) => r.data?.data) : null,
        [bid],
    );

    const { data: utilityTrend, loading: utilLoading, error: utilError } = useBI<{
        months?: Array<{ month?: string; types?: Record<string, number>; total?: number; has_spike?: boolean }>;
    }>(
        bid ? () => api.get(`/bi/building/${bid}/utility-trend?months=12`).then((r: any) => r.data?.data) : null,
        [bid],
    );

    const { data: occupancy, loading: occLoading, error: occError } = useBI(
        bid ? () => api.get(`/bi/building/${bid}/occupancy-mix`).then((r: any) => r.data?.data) : null,
        [bid],
    );

    const { data: heatmap, loading: heatLoading, error: heatError } = useBI(
        bid ? () => api.get(`/bi/building/${bid}/request-heatmap?months=12`).then((r: any) => r.data?.data) : null,
        [bid],
    );

    const { data: assetRisk, loading: assetLoading, error: assetError } = useBI(
        bid ? () => api.get(`/bi/building/${bid}/asset-risk`).then((r: any) => r.data?.data) : null,
        [bid],
    );

    const { data: alerts, loading: alertLoading } = useBI(
        bid ? () => api.get(`/bi/building/${bid}/alerts`).then((r: any) => r.data) : null,
        [bid],
    );

    // ── Derived data ──────────────────────────────────────────────────────

    const levyTrendChartData = useMemo(() => {
        if (!Array.isArray(levyTrend)) return [];
        return levyTrend.map((r: any) => ({
            month: r.month,
            Charged: r.charged || 0,
            Paid: r.paid || 0,
            rate: r.collection_rate,
        }));
    }, [levyTrend]);

    const capexChartData = useMemo(() => {
        if (!Array.isArray(capex)) return [];
        return capex.map((r: any) => ({
            year: String(r.year),
            Planned: r.planned || 0,
            Actual: r.actual || null,
        }));
    }, [capex]);

    const sfChartData = useMemo(() => {
        if (!sfProjection?.projection) return [];
        return sfProjection.projection.map((r: any) => ({
            period: r.year || r.date?.slice(0, 7) || "",
            Balance: r.balance || 0,
        }));
    }, [sfProjection]);

    const healthChartData = useMemo(() => {
        if (!Array.isArray(healthTrend)) return [];
        return healthTrend.map((r: any) => ({
            date: r.date?.slice(0, 10) || "",
            Score: r.overall_score || 0,
            Financial: r.financial_score || 0,
            Maintenance: r.maintenance_score || 0,
            Compliance: r.compliance_score || 0,
        }));
    }, [healthTrend]);

    const maintCategoryData = useMemo(() => {
        if (!maintCosts?.by_category) return [];
        return maintCosts.by_category.slice(0, 8).map((r: any) => ({
            name: r.category || "Other",
            Cost: r.total_cost || 0,
            WOs: r.work_orders || 0,
        }));
    }, [maintCosts]);

    const utilChartData = useMemo(() => {
        if (!utilityTrend?.months) return [];
        return utilityTrend.months.map((m: any) => ({
            month: m.month,
            Water: m.types?.water || 0,
            Electricity: m.types?.electricity || 0,
            Gas: m.types?.gas || 0,
            Rates: m.types?.council_rates || 0,
            Total: m.total || 0,
            spike: m.has_spike,
        }));
    }, [utilityTrend]);

    const heatmapCategories = useMemo(() => {
        if (!Array.isArray(heatmap)) return [];
        return [...new Set(heatmap.map((r: any) => r.category))];
    }, [heatmap]);

    const heatmapMonths = useMemo(() => {
        if (!Array.isArray(heatmap)) return [];
        return [...new Set(heatmap.map((r: any) => r.month))].sort();
    }, [heatmap]);

    if (!authLoading && !canViewPage) return null;

    const alertCount = (alerts as any)?.alert_count || 0;
    const sourceLabel = pgPrimary ? "PostgreSQL Primary" :
        (finSummary as any)?.source === "postgres_bi" ? "Canonical BI" : "Transitional / Mongo";

    // ── Disabled state ────────────────────────────────────────────────────────
    if (!authLoading && user && !toggleOn) {
        const isAdmin = ["super_admin", "strata_admin"].includes(effectiveRole);
        return (
            <div className="flex min-h-[60vh] flex-col items-center justify-center gap-6 p-8">
                <div className="rounded-xl border border-border bg-card p-8 text-center shadow-sm max-w-md w-full">
                    <Database className="mx-auto mb-4 h-10 w-10 text-muted-foreground" aria-hidden="true" />
                    <h2 className="text-xl font-semibold text-foreground">BI Analytics is not enabled</h2>
                    <p className="mt-2 text-sm text-muted-foreground">
                        {isAdmin
                            ? "Enable bi_analytics_building_enabled in Feature Toggles, then run an ETL sync via /api/bi/admin/etl/run."
                            : "Analytics is not currently enabled for this building. Contact your strata manager."}
                    </p>
                    {isAdmin && (
                        <Button
                            className="mt-4"
                            variant="outline"
                            onClick={() => router.push("/admin/feature-toggles")}
                        >
                            Manage Feature Toggles
                        </Button>
                    )}
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-8 pb-20">

            {/* ── Transitional mode banner ── */}
            {!pgPrimary && toggleOn && (
                <div className="flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    <span>
                        <strong>Transitional analytics mode</strong> — data sourced from MongoDB fallback.
                        Run{" "}
                        <code className="rounded bg-amber-100 px-1 text-xs">GET /api/bi/building/{bid}/cutover-status</code>{" "}
                        to check PG-primary readiness.
                        {biWarnings.length > 0 && <> · {biWarnings.join(" · ")}</>}
                    </span>
                </div>
            )}

            <PageHeader
                title="BI Analytics"
                icon={<Database className="h-5 w-5" />}
                description={
                    `Canonical cross-domain intelligence for ${selectedBuilding?.name || "your building"}. ` +
                    "All panels read from verified fact tables \u2014 every number has a traceable source, " +
                    "grain, and refresh time."
                }
                badges={
                    <>
                        <Badge variant="secondary">Data Intelligence Platform</Badge>
                        <Badge variant={pgPrimary ? "default" : "outline"}>{sourceLabel}</Badge>
                        {alertCount > 0 && (
                            <Badge variant="destructive">
                                {alertCount} Active Alert{alertCount !== 1 ? "s" : ""}
                            </Badge>
                        )}
                    </>
                }
                actions={
                    <>
                        <Button variant="outline" size="sm" onClick={() => router.push("/intelligence/financial")}>
                            Finance Deep Dive
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => router.push("/intelligence/assets")}>
                            Asset Intelligence
                        </Button>
                    </>
                }
            />

            {/* ── Panel 1: Financial Health KPIs ── */}
            <section>
                <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Financial Health</h2>
                <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                    <KpiTile
                        title="Collection Rate"
                        value={pct(finSummary?.levy_collection_rate)}
                        sub={`${fmtK(finSummary?.total_paid)} of ${fmtK(finSummary?.total_levied)}`}
                        icon={BarChart3}
                        color="emerald"
                        loading={finLoading}
                        trend={(finSummary?.levy_collection_rate || 0) >= 95 ? "up" : "down"}
                    />
                    <KpiTile
                        title="Total Arrears"
                        value={fmtK(finSummary?.total_arrears)}
                        sub={`${finSummary?.lots_in_arrears ?? "—"} lots overdue`}
                        icon={AlertTriangle}
                        color={(finSummary?.lots_in_arrears || 0) > 0 ? "red" : "emerald"}
                        loading={finLoading}
                    />
                    <KpiTile
                        title="Admin Fund"
                        value={fmtK(finSummary?.admin_fund_balance)}
                        sub="Current balance"
                        icon={ShieldCheck}
                        color="blue"
                        loading={finLoading}
                    />
                    <KpiTile
                        title="Sinking Fund"
                        value={fmtK(finSummary?.sinking_fund_balance)}
                        sub={sfProjection?.shortfall_risk ? "⚠ Shortfall risk" : "On track"}
                        icon={HardDriveDownload}
                        color={sfProjection?.shortfall_risk ? "amber" : "violet"}
                        loading={finLoading || sfLoading}
                        trend={sfProjection?.shortfall_risk ? "down" : "up"}
                    />
                </div>
            </section>

            {/* ── Panel 2: Levy Collection Trend ── */}
            <PanelShell
                title="Levy Collection Trend"
                description="Monthly levied vs paid — last 12 months"
                drilldown="/financials"
                loading={trendLoading}
                error={trendError}
                empty={levyTrendChartData.length === 0}
            >
                <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={levyTrendChartData} margin={{ left: 0, right: 8 }}>
                        <CartesianGrid {...gridProps} />
                        <XAxis {...axisProps} dataKey="month" tickLine={false} />
                        <YAxis {...axisProps} tickFormatter={(v) => `$${Math.round(v / 1000)}k`} />
                        <RechartsTooltip {...tooltipProps} formatter={(v: any, name: string | undefined) => [formatCurrency(Number(v)), name ?? ""]} />
                        <Bar dataKey="Charged" fill={CHART_SERIES[2]} radius={barRadius} />
                        <Bar dataKey="Paid" fill={CHART_SERIES[0]} radius={barRadius} />
                    </BarChart>
                </ResponsiveContainer>
            </PanelShell>

            {/* ── Panels 3 + 4 side by side ── */}
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">

                {/* Panel 3: Arrears Risk Board */}
                <PanelShell
                    title="Arrears Risk Board"
                    description="Lots with outstanding balances — latest snapshot"
                    drilldown="/financials?tab=arrears"
                    loading={arrearsLoading}
                    error={arrearsError}
                    empty={!arrears || (arrears as any[]).length === 0}
                >
                    <div className="overflow-hidden rounded-xl border border-border">
                        <Table data-testid="arrears-table">
                            <TableHeader>
                                <TableRow>
                                    <TableHead className="text-left font-semibold">Lot</TableHead>
                                    <TableHead className="text-right font-semibold">Outstanding</TableHead>
                                    <TableHead className="text-center font-semibold">Days</TableHead>
                                    <TableHead className="text-center font-semibold">Band</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {((arrears || []) as any[]).slice(0, 8).map((row: any) => (
                                    <TableRow key={row.lot_number}>
                                        <TableCell className="font-semibold text-foreground">
                                            {row.lot_number}
                                            {row.owner_name && (
                                                <span className="ml-1 text-muted-foreground">· {row.owner_name}</span>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-right font-semibold text-red-700">
                                            {formatCurrency(row.total_outstanding)}
                                        </TableCell>
                                        <TableCell className="text-center text-muted-foreground">
                                            {row.days_overdue ?? "—"}
                                        </TableCell>
                                        <TableCell className="text-center">
                                            {ragBadge(
                                                row.days_overdue >= 90 ? "red"
                                                : row.days_overdue >= 30 ? "amber"
                                                : "green"
                                            )}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </div>
                </PanelShell>

                {/* Panel 11: Occupancy Mix */}
                <PanelShell
                    title="Occupancy Mix"
                    description="Owner-occupier / tenant / investor / vacant"
                    drilldown="/admin/strata-roll"
                    loading={occLoading}
                    error={occError}
                    empty={!occupancy || (occupancy as any).by_type?.length === 0}
                >
                    <div className="flex gap-4">
                        <ResponsiveContainer width={160} height={160}>
                            <PieChart>
                                <Pie
                                    data={(occupancy as any)?.by_type || []}
                                    dataKey="count"
                                    nameKey="type"
                                    innerRadius={50}
                                    outerRadius={75}
                                    paddingAngle={3}
                                >
                                    {((occupancy as any)?.by_type || []).map((_: any, i: number) => (
                                        <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                                    ))}
                                </Pie>
                                <RechartsTooltip {...tooltipProps} formatter={(v: any, n: string | undefined) => [`${v} lots`, n ?? ""]} />
                            </PieChart>
                        </ResponsiveContainer>
                        <div className="flex flex-col justify-center gap-2">
                            {((occupancy as any)?.by_type || []).map((t: any, i: number) => (
                                <div key={t.type} className="flex items-center gap-2 text-xs">
                                    <span className="h-2 w-2 rounded-full" style={{ background: CHART_COLORS[i % CHART_COLORS.length] }} />
                                    <span className="font-semibold capitalize text-foreground">
                                        {t.type.replace(/_/g, " ")}
                                    </span>
                                    <span className="text-muted-foreground">
                                        {t.count} ({pct(t.pct, 0)})
                                    </span>
                                </div>
                            ))}
                        </div>
                    </div>
                </PanelShell>
            </div>

            {/* ── Panels 4 + 5: Capital Works + Sinking Fund ── */}
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">

                {/* Panel 4: Capex vs Plan */}
                <PanelShell
                    title="Capital Works: Spend vs Plan"
                    description="Planned vs actual capital expenditure by year"
                    drilldown="/intelligence/financial?tab=capital"
                    loading={capexLoading}
                    error={capexError}
                    empty={capexChartData.length === 0}
                >
                    <ResponsiveContainer width="100%" height={200}>
                        <BarChart data={capexChartData}>
                            <CartesianGrid {...gridProps} />
                            <XAxis {...axisProps} dataKey="year" tickLine={false} />
                            <YAxis {...axisProps} tickFormatter={(v) => `$${Math.round(v / 1000)}k`} />
                            <RechartsTooltip {...tooltipProps} formatter={(v: any, n: string | undefined) => [formatCurrency(Number(v)), n ?? ""]} />
                            <Bar dataKey="Planned" fill={CHART_SERIES[2]} radius={barRadius} />
                            <Bar dataKey="Actual" fill={CHART_SERIES[0]} radius={barRadius} />
                        </BarChart>
                    </ResponsiveContainer>
                </PanelShell>

                {/* Panel 5: Sinking Fund Runway */}
                <PanelShell
                    title="Sinking Fund Runway"
                    description="10-year balance projection with shortfall risk"
                    drilldown="/intelligence/financial?tab=sinking"
                    loading={sfLoading}
                    error={sfError}
                    empty={sfChartData.length === 0}
                    badge={sfProjection?.shortfall_risk ? (
                        <Badge className="bg-red-100 text-red-700 border-red-200 text-xs">Shortfall Risk</Badge>
                    ) : (
                        <Badge className="bg-emerald-100 text-emerald-700 border-emerald-200 text-xs">On Track</Badge>
                    )}
                >
                    <ResponsiveContainer width="100%" height={200}>
                        <AreaChart data={sfChartData}>
                            <defs>
                                <linearGradient id="sfGrad" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor={CHART_SERIES[0]} stopOpacity={0.15} />
                                    <stop offset="95%" stopColor={CHART_SERIES[0]} stopOpacity={0} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid {...gridProps} />
                            <XAxis {...axisProps} dataKey="period" tickLine={false} />
                            <YAxis {...axisProps} tickFormatter={(v) => `$${Math.round(v / 1000)}k`} />
                            <RechartsTooltip {...tooltipProps} formatter={(v: any) => [formatCurrency(Number(v)), "Balance"]} />
                            <Area type="monotone" dataKey="Balance" stroke={CHART_SERIES[0]} fill="url(#sfGrad)" strokeWidth={2} dot={false} />
                        </AreaChart>
                    </ResponsiveContainer>
                </PanelShell>
            </div>

            {/* ── Panel 6: Building Health Trend ── */}
            <PanelShell
                title="Building Health Trend"
                description="Overall score and component dimensions — last 90 days"
                drilldown="/intelligence/global-risk"
                loading={healthLoading}
                error={healthError}
                empty={healthChartData.length === 0}
            >
                <ResponsiveContainer width="100%" height={220}>
                    <LineChart data={healthChartData}>
                        <CartesianGrid {...gridProps} />
                        <XAxis {...axisProps} dataKey="date" tickLine={false} />
                        <YAxis {...axisProps} domain={[0, 100]} axisLine={false} tickLine={false} />
                        <RechartsTooltip {...tooltipProps} />
                        <Line type="monotone" dataKey="Score" stroke={CHART_SERIES[0]} strokeWidth={2.5} dot={false} />
                        <Line type="monotone" dataKey="Financial" stroke={CHART_STATUS.good} strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
                        <Line type="monotone" dataKey="Maintenance" stroke={CHART_STATUS.warning} strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
                        <Line type="monotone" dataKey="Compliance" stroke={CHART_SERIES[2]} strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
                    </LineChart>
                </ResponsiveContainer>
            </PanelShell>

            {/* ── Panels 7 + 9: Maintenance Costs + Compliance ── */}
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">

                {/* Panel 7: Maintenance Cost Trend */}
                <PanelShell
                    title="Maintenance Costs"
                    description={`FY ${fy} · by category · ${maintCosts?.total_work_orders ?? 0} work orders`}
                    drilldown="/maintenance"
                    loading={maintLoading}
                    error={maintError}
                    empty={maintCategoryData.length === 0}
                >
                    <div className="mb-3 flex gap-4 text-xs">
                        <div>
                            <span className="font-semibold text-foreground">{fmtK(maintCosts?.total_cost)}</span>
                            <span className="ml-1 text-muted-foreground">total spend</span>
                        </div>
                        <div>
                            <span className="font-semibold text-foreground">{maintCosts?.open_work_orders ?? "—"}</span>
                            <span className="ml-1 text-muted-foreground">open</span>
                        </div>
                        {(maintCosts?.sla_breaches || 0) > 0 && (
                            <div>
                                <span className="font-semibold text-red-700">{maintCosts?.sla_breaches}</span>
                                <span className="ml-1 text-muted-foreground">SLA breaches</span>
                            </div>
                        )}
                    </div>
                    <ResponsiveContainer width="100%" height={160}>
                        <BarChart data={maintCategoryData} layout="vertical">
                            <XAxis {...axisProps} type="number" tickFormatter={(v) => `$${Math.round(v / 1000)}k`} />
                            <YAxis {...axisProps} type="category" dataKey="name" width={90} axisLine={false} tickLine={false} />
                            <RechartsTooltip {...tooltipProps} formatter={(v: any) => [formatCurrency(Number(v)), "Cost"]} />
                            <Bar dataKey="Cost" fill={CHART_SERIES[0]} radius={[0, 6, 6, 0]} />
                        </BarChart>
                    </ResponsiveContainer>
                </PanelShell>

                {/* Panel 9: Compliance Status RAG Grid */}
                <PanelShell
                    title="Compliance Status"
                    description="RAG status by register type"
                    drilldown="/compliance"
                    loading={compLoading}
                    error={compError}
                    empty={!complianceStatus || (complianceStatus as any).registers?.length === 0}
                    badge={ragBadge((complianceStatus as any)?.overall_status)}
                >
                    <div className="space-y-2">
                        {((complianceStatus as any)?.registers || []).map((reg: any) => (
                            <div key={reg.register_type} className="flex items-center justify-between rounded-xl bg-muted px-3 py-2">
                                <div>
                                    <span className="text-xs font-semibold text-foreground capitalize">
                                        {reg.register_type?.replace(/_/g, " ") || "Unknown"}
                                    </span>
                                    {reg.next_due && (
                                        <span className="ml-2 text-xs text-muted-foreground">due {reg.next_due}</span>
                                    )}
                                </div>
                                <div className="flex items-center gap-1.5">
                                    {reg.green > 0 && <span className="rounded-full bg-emerald-100 px-1.5 py-0.5 text-xs font-bold text-emerald-700">{reg.green}✓</span>}
                                    {reg.amber > 0 && <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-xs font-bold text-amber-700">{reg.amber}!</span>}
                                    {reg.red > 0 && <span className="rounded-full bg-red-100 px-1.5 py-0.5 text-xs font-bold text-red-700">{reg.red}✗</span>}
                                </div>
                            </div>
                        ))}
                    </div>
                </PanelShell>
            </div>

            {/* ── Panel 10: Utility Spend Trend ── */}
            <PanelShell
                title="Utility Spend Trend"
                description="Monthly spend by type — last 12 months. Spike flag = >20% above rolling average."
                drilldown="/financials/council-rates"
                loading={utilLoading}
                error={utilError}
                empty={utilChartData.length === 0}
            >
                <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={utilChartData}>
                        <CartesianGrid {...gridProps} />
                        <XAxis {...axisProps} dataKey="month" tickLine={false} />
                        <YAxis {...axisProps} tickFormatter={(v) => `$${Math.round(v / 1000)}k`} />
                        <RechartsTooltip {...tooltipProps} formatter={(v: any, n: string | undefined) => [formatCurrency(Number(v)), n ?? ""]} />
                        <Bar dataKey="Water" stackId="a" fill={CHART_SERIES[2]} />
                        <Bar dataKey="Electricity" stackId="a" fill={CHART_SERIES[0]} />
                        <Bar dataKey="Gas" stackId="a" fill={CHART_SERIES[1]} />
                        <Bar dataKey="Rates" stackId="a" fill={CHART_SERIES[4]} radius={barRadius} />
                    </BarChart>
                </ResponsiveContainer>
            </PanelShell>

            {/* ── Panel 12: Smart Request Heatmap ── */}
            <PanelShell
                title="Smart Request Heatmap"
                description="Request volume by category × month — patterns reveal recurring problems"
                drilldown="/requests/new"
                loading={heatLoading}
                error={heatError}
                empty={!heatmap || (heatmap as any[]).length === 0}
            >
                <div className="overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead className="w-32 text-left text-muted-foreground">Category</TableHead>
                                {heatmapMonths.slice(-6).map((m) => (
                                    <TableHead key={m} className="text-center text-muted-foreground">{m.slice(5)}</TableHead>
                                ))}
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {heatmapCategories.slice(0, 8).map((cat) => (
                                <TableRow key={cat} className="border-t border-border">
                                    <TableCell className="font-semibold text-foreground capitalize">
                                        {String(cat).replace(/_/g, " ")}
                                    </TableCell>
                                    {heatmapMonths.slice(-6).map((m) => {
                                        const cell = (heatmap as any[]).find((r) => r.category === cat && r.month === m);
                                        const count = cell?.count || 0;
                                        // Intensity saturates at 5 events/month — the ramp's top step
                                        // means ">= 5", not "the maximum in this dataset", so the scale
                                        // stays stable as data changes.
                                        const intensity = Math.min(count / 5, 1);
                                        // Foreground comes from the ramp's per-step ink table, never from
                                        // an intensity threshold: contrast is a property of the STEP.
                                        // (A `intensity > 0.5 ? white : black` rule fails WCAG AA across
                                        // the middle of this ramp — white on #00a2ad is only 3.10:1.)
                                        const swatch = sequentialStep(intensity);
                                        return (
                                            <TableCell key={m} className="text-center">
                                                <span
                                                    className="inline-flex h-7 w-7 items-center justify-center rounded-lg font-semibold"
                                                    style={{
                                                        // Zero is an empty surface, not the palest ramp
                                                        // step, so "no events" stays visually distinct
                                                        // from "the fewest events".
                                                        background: count === 0 ? "hsl(60 10% 95%)" : swatch.bg,
                                                        color: count === 0 ? CHART_INK.label : swatch.fg,
                                                    }}
                                                    title={`${String(cat).replace(/_/g, " ")}, ${m}: ${count} event${count === 1 ? "" : "s"}`}
                                                >
                                                    {count || ""}
                                                </span>
                                            </TableCell>
                                        );
                                    })}
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            </PanelShell>

            {/* ── Panel 13: Asset Risk ── */}
            <PanelShell
                title="Asset Risk Register"
                description="Assets by condition score — lowest condition first"
                drilldown="/intelligence/assets"
                loading={assetLoading}
                error={assetError}
                empty={!assetRisk || (assetRisk as any[]).length === 0}
            >
                <div className="overflow-hidden rounded-xl border border-border">
                    <Table data-testid="asset-risk-table">
                        <TableHeader>
                            <TableRow>
                                <TableHead className="text-left font-semibold">Asset</TableHead>
                                <TableHead className="text-center font-semibold">Score</TableHead>
                                <TableHead className="text-center font-semibold">Remaining Life</TableHead>
                                <TableHead className="text-right font-semibold">Replacement</TableHead>
                                <TableHead className="text-center font-semibold">Risk</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {((assetRisk || []) as any[]).slice(0, 8).map((a: any, i: number) => (
                                <TableRow key={i}>
                                    <TableCell >
                                        <span className="font-semibold text-foreground">{a.asset_name}</span>
                                        {a.category && <span className="ml-1 text-muted-foreground">· {a.category}</span>}
                                    </TableCell>
                                    <TableCell className="text-center">
                                        <span className={`font-bold ${a.condition_score < 4 ? "text-red-700" : a.condition_score < 7 ? "text-amber-700" : "text-emerald-700"}`}>
                                            {a.condition_score?.toFixed(1) ?? "—"}/10
                                        </span>
                                    </TableCell>
                                    <TableCell className="text-center text-muted-foreground">
                                        {a.remaining_life_years ? `${a.remaining_life_years}y` : "—"}
                                    </TableCell>
                                    <TableCell className="text-right text-foreground">
                                        {a.replacement_cost ? formatCurrency(a.replacement_cost) : "—"}
                                    </TableCell>
                                    <TableCell className="text-center">
                                        {ragBadge(
                                            a.risk_band === "critical" || a.risk_band === "high" ? "red"
                                            : a.risk_band === "medium" ? "amber"
                                            : "green"
                                        )}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            </PanelShell>

            {/* ── Active Alerts Banner ── */}
            {!alertLoading && alertCount > 0 && (
                <section className="rounded-2xl border border-red-200 bg-red-50 p-5">
                    <div className="flex items-center gap-3 mb-3">
                        <AlertTriangle className="h-5 w-5 text-red-700" />
                        <h3 className="font-semibold text-red-900">{alertCount} Active Alert{alertCount !== 1 ? "s" : ""}</h3>
                    </div>
                    <div className="space-y-2">
                        {((alerts as any)?.alerts || []).slice(0, 5).map((alert: any, i: number) => (
                            <div key={i} className="flex items-start gap-3 rounded-xl bg-card p-3 text-sm">
                                <span className={`mt-0.5 h-2 w-2 rounded-full shrink-0 ${alert.severity === "high" ? "bg-red-500" : "bg-amber-500"}`} />
                                <div>
                                    <span className="font-semibold text-foreground capitalize">
                                        {alert.rule_code?.replace(/_/g, " ")}
                                    </span>
                                    {alert.entity_id && (
                                        <span className="ml-2 text-muted-foreground">· {alert.entity_type} {alert.entity_id}</span>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </section>
            )}

            {/* ── Data lineage footer ── */}
            <section className="rounded-2xl border border-border bg-muted px-6 py-4">
                <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1.5">
                        <Database className="h-3.5 w-3.5" />
                        Source: <strong className="text-foreground">{(finSummary as any)?.source || "—"}</strong>
                    </span>
                    <span className="flex items-center gap-1.5">
                        <Layers className="h-3.5 w-3.5" />
                        Schema: <strong className="text-foreground">analytics.fact_*</strong>
                    </span>
                    <span className="flex items-center gap-1.5">
                        <Activity className="h-3.5 w-3.5" />
                        Toggle: <strong className="text-foreground">bi_analytics_enabled</strong>
                    </span>
                    <span>All fact tables have grain, source lineage, and refresh metadata.</span>
                    <button
                        onClick={() => router.push("/admin/analytics")}
                        className="ml-auto flex items-center gap-1 rounded font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                    >
                        ETL Status <ChevronRight className="h-3.5 w-3.5" />
                    </button>
                </div>
            </section>
        </div>
    );
}
