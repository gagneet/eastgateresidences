// @featuretrace:bi-analytics — Strata Manager portfolio BI page: assigned-buildings-only analytics.
// Layer: frontend
// Data flow: ManagerBIAnalyticsPage → GET /api/bi/manager/{id}/* → bi.py → bi_service → analytics.fact_*
// Related: backend/routers/bi.py
//           backend/services/bi_toggle_service.py
// Toggle: bi_portfolio_analytics_enabled
// Tests: tests/frontend/unit/pages/dashboard/ManagerBIAnalyticsPage.test.tsx
"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
    AlertTriangle, BarChart3, Building2, CheckCircle2,
    RefreshCw, ShieldCheck, TrendingDown, Wrench,
} from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatTile } from "@/components/shared/StatTile";
import {
    Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer,
    Tooltip as RechartsTooltip, XAxis, YAxis,
} from "recharts";
import { CHART_STATUS, axisProps, gridProps, tooltipProps } from "@/lib/chartTheme";
import { formatCurrency } from "@/lib/utils";

const MANAGER_ROLES = ["super_admin", "strata_admin", "strata_manager"];
/**
 * @generated FunctionHeader
 * Function: useManagerBI
 * Path: frontend/src/pages/dashboard/ManagerBIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function useManagerBI<T>(fetcher: (() => Promise<T>) | null) {
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
 * Path: frontend/src/pages/dashboard/ManagerBIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function Panel({ title, loading, error, children }: {
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

interface ManagerBIAnalyticsPageProps {
    strataManagerId: string;
}
/**
 * @generated FunctionHeader
 * Function: ManagerBIAnalyticsPage
 * Path: frontend/src/pages/dashboard/ManagerBIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function ManagerBIAnalyticsPage({ strataManagerId }: ManagerBIAnalyticsPageProps) {
    const { api, user, loading: authLoading } = useAuth() as any;
    const router = useRouter();

    const effectiveRole = user?.effective_role || user?.role || "";
    const canView = !!user && MANAGER_ROLES.includes(effectiveRole);

    useEffect(() => {
        if (!authLoading && user && !canView) router.replace("/dashboard");
    }, [authLoading, canView, router, user]);

    const fetchSummary = useCallback(
        () => api.get(`/bi/manager/${strataManagerId}/summary`).then((r: any) => r.data?.data),
        [api, strataManagerId],
    );
    const fetchBuildings = useCallback(
        () => api.get(`/bi/manager/${strataManagerId}/buildings`).then((r: any) => r.data?.data),
        [api, strataManagerId],
    );
    const fetchHealth = useCallback(
        () => api.get(`/bi/manager/${strataManagerId}/health-ranking`).then((r: any) => r.data?.data),
        [api, strataManagerId],
    );
    const fetchWorkload = useCallback(
        () => api.get(`/bi/manager/${strataManagerId}/workload`).then((r: any) => r.data?.data),
        [api, strataManagerId],
    );
    const fetchArrears = useCallback(
        () => api.get(`/bi/manager/${strataManagerId}/arrears-hotspots`).then((r: any) => r.data?.data),
        [api, strataManagerId],
    );
    const fetchUpcoming = useCallback(
        () => api.get(`/bi/manager/${strataManagerId}/upcoming-actions`).then((r: any) => r.data?.data),
        [api, strataManagerId],
    );

    const { data: summary, loading: sumLoading, refresh } = useManagerBI(
        strataManagerId && canView ? fetchSummary : null,
    );

    const { data: buildings, loading: bldgLoading } = useManagerBI(
        strataManagerId && canView ? fetchBuildings : null,
    );

    const { data: health, loading: healthLoading } = useManagerBI(
        strataManagerId && canView ? fetchHealth : null,
    );

    const { data: workload, loading: workloadLoading } = useManagerBI(
        strataManagerId && canView ? fetchWorkload : null,
    );

    const { data: arrears, loading: arrearsLoading } = useManagerBI(
        strataManagerId && canView ? fetchArrears : null,
    );

    const { data: upcoming, loading: upcomingLoading } = useManagerBI(
        strataManagerId && canView ? fetchUpcoming : null,
    );

    if (!authLoading && !canView) return null;

    const s = summary as any;
    const w = workload as any;
    const healthList = Array.isArray(health) ? health : [];
    const arrearsList = Array.isArray(arrears) ? arrears : [];
    const bldgList = Array.isArray(buildings) ? buildings : [];
    const upcomingList = Array.isArray(upcoming) ? upcoming : [];
    const workloadBuildings = Array.isArray(w?.buildings) ? w.buildings : [];

    return (
        <div className="space-y-8 pb-20">
            <PageHeader
                title="My Portfolio Analytics"
                icon={<BarChart3 className="h-5 w-5" />}
                description={
                    `${s?.building_count ?? bldgList.length} assigned buildings. ` +
                    "You can only see data for your assigned buildings."
                }
                badges={
                    <>
                        <Badge variant="secondary">Strata Manager Portfolio</Badge>
                        <Badge variant="outline">Assigned Buildings Only</Badge>
                    </>
                }
                actions={
                    <Button variant="outline" size="sm" onClick={refresh}>
                        <RefreshCw className="mr-2 h-4 w-4" /> Refresh
                    </Button>
                }
            />

            {/* Workload KPIs */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                {[
                    {
                        title: "Assigned Buildings",
                        value: s?.building_count ?? bldgList.length,
                        icon: Building2, tone: "default" as const,
                        loading: sumLoading || bldgLoading,
                    },
                    {
                        title: "Open Work Orders",
                        value: w?.total_open_work_orders ?? "\u2014",
                        icon: Wrench, tone: "default" as const,
                        loading: workloadLoading,
                    },
                    {
                        title: "SLA Breaches",
                        value: w?.total_sla_breaches ?? "\u2014",
                        icon: TrendingDown,
                        tone: (w?.total_sla_breaches ?? 0) > 0 ? ("critical" as const) : ("good" as const),
                        loading: workloadLoading,
                    },
                    {
                        title: "Compliance Overdue",
                        value: w?.total_compliance_overdue ?? "\u2014",
                        icon: ShieldCheck,
                        tone: (w?.total_compliance_overdue ?? 0) > 0 ? ("warning" as const) : ("good" as const),
                        loading: workloadLoading,
                    },
                ].map(({ title, value, icon: Icon, tone, loading: l }) => (
                    <StatTile
                        key={title}
                        label={title}
                        value={value}
                        tone={tone}
                        loading={l}
                        icon={<Icon className="h-4 w-4" />}
                    />
                ))}
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
                {/* Health ranking */}
                <Panel title="Building Health Ranking" loading={healthLoading}>
                    {healthList.length > 0 ? (
                        <ResponsiveContainer width="100%" height={220}>
                            <BarChart data={healthList} layout="vertical">
                                <CartesianGrid {...gridProps} />
                                <XAxis {...axisProps} type="number" domain={[0, 100]} />
                                <YAxis {...axisProps} type="category" dataKey="building_id" width={80} />
                                <RechartsTooltip {...tooltipProps} />
                                <Bar dataKey="overall_score" radius={[0, 4, 4, 0]}>
                                    {healthList.map((entry: any, i: number) => (
                                        <Cell
                                            key={i}
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

                {/* Workload by building */}
                <Panel title="Workload by Building" loading={workloadLoading}>
                    <div className="space-y-2">
                        {workloadBuildings.slice(0, 10).map((b: any) => (
                            <div key={b.building_id} className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
                                <span className="text-xs text-muted-foreground">{b.building_id}</span>
                                <div className="flex items-center gap-3 text-xs">
                                    <span className="text-muted-foreground">{b.open_work_orders ?? 0} WO</span>
                                    <span className={`font-semibold ${(b.sla_breaches || 0) > 0 ? "text-red-700" : "text-emerald-700"}`}>
                                        {b.sla_breaches ?? 0} SLA
                                    </span>
                                    <span className={`${(b.compliance_overdue || 0) > 0 ? "text-amber-700" : "text-muted-foreground"}`}>
                                        {b.compliance_overdue ?? 0} comp
                                    </span>
                                </div>
                            </div>
                        ))}
                        {workloadBuildings.length === 0 && (
                            <p className="py-8 text-center text-sm text-muted-foreground">No workload data</p>
                        )}
                    </div>
                </Panel>

                {/* Arrears hotspots */}
                <Panel title="Arrears Hotspots — My Buildings" loading={arrearsLoading}>
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
                                {arrearsList.slice(0, 15).map((lot: any, i: number) => (
                                    <TableRow key={i}>
                                        <TableCell className="text-muted-foreground">{lot.building_id}</TableCell>
                                        <TableCell className="font-medium">{lot.lot_number}</TableCell>
                                        <TableCell className="text-right font-medium text-red-700">
                                            {formatCurrency(lot.total_outstanding)}
                                        </TableCell>
                                        <TableCell className="text-right">{lot.days_overdue ?? "\u2014"}</TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                        {arrearsList.length === 0 && (
                            <p className="py-8 text-center text-sm text-muted-foreground">No arrears</p>
                        )}
                    </div>
                </Panel>

                {/* Upcoming compliance actions */}
                <Panel title="Upcoming Actions" loading={upcomingLoading}>
                    <div className="space-y-2">
                        {upcomingList.slice(0, 10).map((b: any) => (
                            <div key={b.building_id} className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
                                <span className="text-xs text-muted-foreground">{b.building_id}</span>
                                <div className="flex items-center gap-2 text-xs">
                                    <span className={`${(b.total_overdue || 0) > 0 ? "font-semibold text-red-700" : "text-muted-foreground"}`}>
                                        {b.total_overdue ?? 0} overdue
                                    </span>
                                    {b.overall_status === "green" && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />}
                                    {b.overall_status === "amber" && <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />}
                                    {b.overall_status === "red" && <ShieldCheck className="h-3.5 w-3.5 text-red-500" />}
                                </div>
                            </div>
                        ))}
                        {upcomingList.length === 0 && (
                            <p className="py-8 text-center text-sm text-muted-foreground">No upcoming actions</p>
                        )}
                    </div>
                </Panel>
            </div>
        </div>
    );
}
