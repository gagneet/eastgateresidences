// @featuretrace:bi-analytics — Agency portfolio BI page: cross-building analytics for agency principals.
// Layer: frontend
// Data flow: AgencyBIAnalyticsPage → GET /api/bi/agency/{id}/* → bi.py → bi_service → analytics.fact_*
// Related: backend/routers/bi.py
//           backend/services/bi_service.py
//           backend/services/bi_toggle_service.py
// Toggle: bi_analytics_agency_enabled
// Tests: tests/frontend/unit/pages/dashboard/AgencyBIAnalyticsPage.test.tsx
"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
    AlertTriangle,
    BarChart3,
    Building2,
    CheckCircle2,
    Database,
    RefreshCw,
    ShieldCheck,
    TrendingDown,
    TrendingUp,
    Wrench,
} from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer,
    Tooltip as RechartsTooltip, XAxis, YAxis,
} from "recharts";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatTile } from "@/components/shared/StatTile";
import { CHART_SERIES, CHART_STATUS, axisProps, gridProps, tooltipProps } from "@/lib/chartTheme";
import { formatCurrency } from "@/lib/utils";

const AGENCY_MANAGER_ROLES = ["super_admin", "strata_admin", "strata_manager"];
const CHART_COLORS = CHART_SERIES;
/**
 * @generated FunctionHeader
 * Function: pct
 * Path: frontend/src/pages/dashboard/AgencyBIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function pct(v?: number | null) {
    return typeof v === "number" ? `${v.toFixed(1)}%` : "—";
}
/**
 * @generated FunctionHeader
 * Function: useAgencyBI
 * Path: frontend/src/pages/dashboard/AgencyBIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function useAgencyBI<T>(
    fetcher: (() => Promise<T>) | null,
): { data: T | null; loading: boolean; error: string | null; refresh: () => void } {
    const [data, setData] = useState<T | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [tick, setTick] = useState(0);
    const refresh = useCallback(() => setTick((t) => t + 1), []);

    useEffect(() => {
        if (!fetcher) { setLoading(false); return; }
        let cancelled = false;
        setLoading(true);
        setError(null);
        fetcher()
            .then((r) => { if (!cancelled) { setData(r); setLoading(false); } })
            .catch((e) => {
                if (!cancelled) {
                    setError(e?.response?.data?.detail || e?.message || "Failed to load");
                    setLoading(false);
                }
            });
        return () => { cancelled = true; };
    }, [tick, fetcher]);

    return { data, loading, error, refresh };
}

/**
 * @generated FunctionHeader
 * Function: Panel
 * Path: frontend/src/pages/dashboard/AgencyBIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function Panel({
    title, loading, error, children,
}: {
    title: string; loading?: boolean; error?: string | null; children: React.ReactNode;
}) {
    return (
        <Card>
            <CardHeader className="pb-2">
                <CardTitle className="text-base font-semibold text-foreground">{title}</CardTitle>
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
                ) : children}
            </CardContent>
        </Card>
    );
}

interface AgencyBIAnalyticsPageProps {
    agencyId: string;
}
/**
 * @generated FunctionHeader
 * Function: AgencyBIAnalyticsPage
 * Path: frontend/src/pages/dashboard/AgencyBIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function AgencyBIAnalyticsPage({ agencyId }: AgencyBIAnalyticsPageProps) {
    const { api, user, loading: authLoading } = useAuth() as any;
    const router = useRouter();

    const effectiveRole = user?.effective_role || user?.role || "";
    const canView = !!user && AGENCY_MANAGER_ROLES.includes(effectiveRole);

    useEffect(() => {
        if (!authLoading && user && !canView) router.replace("/dashboard");
    }, [authLoading, canView, router, user]);

    const fetchSummary = useCallback(
        () => api.get(`/bi/agency/${agencyId}/summary`).then((r: any) => r.data?.data),
        [agencyId, api],
    );
    const fetchFinancials = useCallback(
        () => api.get(`/bi/agency/${agencyId}/financial-summary`).then((r: any) => r.data?.data),
        [agencyId, api],
    );
    const fetchHealthRanking = useCallback(
        () => api.get(`/bi/agency/${agencyId}/health-ranking`).then((r: any) => r.data?.data),
        [agencyId, api],
    );
    const fetchArrears = useCallback(
        () => api.get(`/bi/agency/${agencyId}/arrears-hotspots`).then((r: any) => r.data?.data),
        [agencyId, api],
    );
    const fetchCompliance = useCallback(
        () => api.get(`/bi/agency/${agencyId}/compliance-overdue`).then((r: any) => r.data?.data),
        [agencyId, api],
    );
    const fetchMaintenance = useCallback(
        () => api.get(`/bi/agency/${agencyId}/maintenance-risk`).then((r: any) => r.data?.data),
        [agencyId, api],
    );

    const { data: summary, loading: sumLoading, error: sumError, refresh } = useAgencyBI(
        agencyId && canView ? fetchSummary : null,
    );

    const { data: financials, loading: finLoading } = useAgencyBI(
        agencyId && canView ? fetchFinancials : null,
    );

    const { data: healthRanking, loading: healthLoading } = useAgencyBI(
        agencyId && canView ? fetchHealthRanking : null,
    );

    const { data: arrears, loading: arrearsLoading } = useAgencyBI(
        agencyId && canView ? fetchArrears : null,
    );

    const { data: compliance, loading: compLoading } = useAgencyBI(
        agencyId && canView ? fetchCompliance : null,
    );

    const { data: maint, loading: maintLoading } = useAgencyBI(
        agencyId && canView ? fetchMaintenance : null,
    );

    if (!authLoading && !canView) return null;

    const s = summary as any;
    const finList = Array.isArray(financials) ? financials : [];
    const healthList = Array.isArray(healthRanking) ? healthRanking : [];
    const arrearsList = Array.isArray(arrears) ? arrears : (arrears as any)?.slice?.() ?? [];
    const compList = Array.isArray(compliance) ? compliance : [];
    const maintList = Array.isArray(maint) ? maint : [];

    return (
        <div className="space-y-8 pb-20">
            <PageHeader
                title="Agency BI Analytics"
                icon={<BarChart3 className="h-5 w-5" />}
                description={
                    `Cross-building portfolio intelligence \u2014 ${s?.building_count ?? "\u2026"} buildings. ` +
                    "Data sourced from canonical fact tables."
                }
                badges={
                    <>
                        <Badge variant="secondary">Agency Portfolio</Badge>
                        <Badge variant="outline">Transitional Analytics</Badge>
                    </>
                }
                actions={
                    <Button variant="outline" size="sm" onClick={refresh}>
                        <RefreshCw className="mr-2 h-4 w-4" /> Refresh
                    </Button>
                }
            />

            {/* KPI row */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                {[
                    {
                        title: "Total Levied",
                        value: s ? formatCurrency(s.total_levied) : "\u2014",
                        icon: BarChart3, tone: "default" as const,
                    },
                    {
                        title: "Total Arrears",
                        value: s ? formatCurrency(s.total_arrears) : "\u2014",
                        icon: TrendingDown,
                        tone: (s?.total_arrears ?? 0) > 0 ? ("critical" as const) : ("good" as const),
                    },
                    {
                        title: "Avg Collection",
                        value: s ? pct(s.avg_collection_rate) : "\u2014",
                        icon: TrendingUp, tone: "default" as const,
                    },
                    {
                        title: "Buildings",
                        value: s?.building_count ?? "\u2014",
                        icon: Building2, tone: "default" as const,
                    },
                ].map(({ title, value, icon: Icon, tone }) => (
                    <StatTile
                        key={title}
                        label={title}
                        value={value}
                        tone={tone}
                        loading={sumLoading}
                        icon={<Icon className="h-4 w-4" />}
                    />
                ))}
            </div>

            {/* Main grid */}
            <div className="grid gap-6 lg:grid-cols-2">
                {/* Financial summary by building */}
                <Panel title="Financial Summary — Per Building" loading={finLoading}>
                    <div className="max-h-64 overflow-y-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Building</TableHead>
                                    <TableHead className="text-right">Levied</TableHead>
                                    <TableHead className="text-right">Collection</TableHead>
                                    <TableHead className="text-right">Arrears</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {finList.map((row: any) => (
                                    <TableRow key={row.building_id}>
                                        <TableCell className="text-muted-foreground">{row.building_id}</TableCell>
                                        <TableCell className="text-right">{formatCurrency(row.total_levied)}</TableCell>
                                        <TableCell className="text-right">{pct(row.levy_collection_rate)}</TableCell>
                                        <TableCell className={`text-right font-medium ${(row.total_arrears || 0) > 0 ? "text-red-700" : "text-muted-foreground"}`}>
                                            {formatCurrency(row.total_arrears)}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                        {finList.length === 0 && (
                            <p className="py-8 text-center text-sm text-muted-foreground">No financial data available</p>
                        )}
                    </div>
                </Panel>

                {/* Health ranking */}
                <Panel title="Building Health Ranking" loading={healthLoading}>
                    {healthList.length > 0 ? (
                        <ResponsiveContainer width="100%" height={220}>
                            <BarChart data={healthList} layout="vertical">
                                <CartesianGrid {...gridProps} />
                                <XAxis {...axisProps} type="number" domain={[0, 100]} />
                                <YAxis {...axisProps} type="category" dataKey="building_id" width={80} />
                                <RechartsTooltip {...tooltipProps} formatter={(v: any) => [`${v}`, "Score"]} />
                                <Bar dataKey="overall_score" radius={[0, 4, 4, 0]}>
                                    {healthList.map((entry: any, i: number) => (
                                        <Cell
                                            key={`cell-${i}`}
                                            fill={
                                                (entry.overall_score ?? 0) >= 80 ? CHART_STATUS.good
                                                    : (entry.overall_score ?? 0) >= 60 ? CHART_STATUS.warning
                                                    : CHART_STATUS.critical
                                            }
                                        />
                                    ))}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    ) : (
                        <p className="py-8 text-center text-sm text-muted-foreground">No health data available</p>
                    )}
                </Panel>

                {/* Arrears hotspots */}
                <Panel title="Arrears Hotspots — Top Lots" loading={arrearsLoading}>
                    <div className="max-h-64 overflow-y-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Building</TableHead>
                                    <TableHead>Lot</TableHead>
                                    <TableHead className="text-right">Outstanding</TableHead>
                                    <TableHead className="text-right">Days</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {arrearsList.slice(0, 20).map((lot: any, i: number) => (
                                    <TableRow key={i}>
                                        <TableCell className="text-muted-foreground">{lot.building_id}</TableCell>
                                        <TableCell className="font-medium">{lot.lot_number}</TableCell>
                                        <TableCell className="text-right font-medium text-red-700">
                                            {formatCurrency(lot.total_outstanding)}
                                        </TableCell>
                                        <TableCell className="text-right text-muted-foreground">{lot.days_overdue ?? "\u2014"}</TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                        {arrearsList.length === 0 && (
                            <p className="py-8 text-center text-sm text-muted-foreground">No arrears data</p>
                        )}
                    </div>
                </Panel>

                {/* Compliance overdue */}
                <Panel title="Compliance Overdue — By Building" loading={compLoading}>
                    <div className="space-y-2">
                        {compList.slice(0, 10).map((b: any) => (
                            <div key={b.building_id} className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
                                <span className="text-xs text-muted-foreground">{b.building_id}</span>
                                <div className="flex items-center gap-2">
                                    <span className={`text-xs font-semibold ${(b.total_overdue || 0) > 0 ? "text-red-700" : "text-emerald-700"}`}>
                                        {b.total_overdue ?? 0} overdue
                                    </span>
                                    {b.overall_status === "green" && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />}
                                    {b.overall_status === "amber" && <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />}
                                    {b.overall_status === "red" && <ShieldCheck className="h-3.5 w-3.5 text-red-500" />}
                                </div>
                            </div>
                        ))}
                        {compList.length === 0 && (
                            <p className="py-8 text-center text-sm text-muted-foreground">No compliance data</p>
                        )}
                    </div>
                </Panel>

                {/* Maintenance risk */}
                <Panel title="Maintenance Risk — SLA Breaches" loading={maintLoading}>
                    <div className="space-y-2">
                        {maintList.slice(0, 10).map((b: any) => (
                            <div key={b.building_id} className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
                                <div className="flex items-center gap-2">
                                    <Wrench className="h-3.5 w-3.5 text-muted-foreground" />
                                    <span className="text-xs text-muted-foreground">{b.building_id}</span>
                                </div>
                                <div className="flex items-center gap-3 text-xs">
                                    <span className="text-muted-foreground">{b.open_work_orders ?? 0} open</span>
                                    <span className={`font-semibold ${(b.sla_breaches || 0) > 0 ? "text-red-700" : "text-emerald-700"}`}>
                                        {b.sla_breaches ?? 0} SLA breach{(b.sla_breaches ?? 0) !== 1 ? "es" : ""}
                                    </span>
                                </div>
                            </div>
                        ))}
                        {maintList.length === 0 && (
                            <p className="py-8 text-center text-sm text-muted-foreground">No maintenance data</p>
                        )}
                    </div>
                </Panel>
            </div>
        </div>
    );
}
