// @featuretrace:bi-analytics — Platform BI page: super-admin cross-agency analytics.
// Layer: frontend
// Data flow: PlatformBIAnalyticsPage → GET /api/bi/platform/* → bi.py → bi_service → analytics.fact_*
// Related: backend/routers/bi.py
//           backend/services/bi_toggle_service.py
// Toggle: bi_analytics_platform_enabled
// Tests: tests/frontend/unit/pages/dashboard/PlatformBIAnalyticsPage.test.tsx
"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
    AlertTriangle, BarChart3, Building2, CheckCircle2,
    Database, Globe, RefreshCw, ShieldCheck, Zap,
} from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatTile } from "@/components/shared/StatTile";
import { CHART_SERIES, CHART_STATUS, axisProps, gridProps, tooltipProps } from "@/lib/chartTheme";
import { formatCurrency } from "@/lib/utils";
/**
 * @generated FunctionHeader
 * Function: usePlatformBI
 * Path: frontend/src/pages/dashboard/PlatformBIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function usePlatformBI<T>(fetcher: (() => Promise<T>) | null) {
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
                    setError(e?.response?.data?.detail || e?.message || "Failed");
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
 * Path: frontend/src/pages/dashboard/PlatformBIAnalyticsPage.tsx
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
/**
 * @generated FunctionHeader
 * Function: PlatformBIAnalyticsPage
 * Path: frontend/src/pages/dashboard/PlatformBIAnalyticsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function PlatformBIAnalyticsPage() {
    const { api, user, loading: authLoading } = useAuth() as any;
    const router = useRouter();

    const effectiveRole = user?.effective_role || user?.role || "";
    const canView = effectiveRole === "super_admin";

    useEffect(() => {
        if (!authLoading && user && !canView) router.replace("/dashboard");
    }, [authLoading, canView, router, user]);

    const fetchSummary = useCallback(
        () => api.get("/bi/platform/summary").then((r: any) => r.data?.data),
        [api],
    );
    const fetchAgencies = useCallback(
        () => api.get("/bi/platform/agencies").then((r: any) => r.data?.data),
        [api],
    );
    const fetchBuildings = useCallback(
        () => api.get("/bi/platform/buildings").then((r: any) => r.data?.data),
        [api],
    );
    const fetchAdoption = useCallback(
        () => api.get("/bi/platform/feature-adoption").then((r: any) => r.data?.data),
        [api],
    );
    const fetchEtlHealth = useCallback(
        () => api.get("/bi/platform/etl-health").then((r: any) => r.data?.data),
        [api],
    );
    const fetchFreshness = useCallback(
        () => api.get("/bi/platform/data-freshness").then((r: any) => r.data?.data),
        [api],
    );

    const { data: summary, loading: sumLoading, refresh } = usePlatformBI(
        canView ? fetchSummary : null,
    );
    const { data: agencies, loading: agencyLoading } = usePlatformBI(
        canView ? fetchAgencies : null,
    );
    const { data: buildings, loading: bldgLoading } = usePlatformBI(
        canView ? fetchBuildings : null,
    );
    const { data: adoption, loading: adoptLoading } = usePlatformBI(
        canView ? fetchAdoption : null,
    );
    const { data: etlHealth, loading: etlLoading } = usePlatformBI(
        canView ? fetchEtlHealth : null,
    );
    const { data: freshness, loading: freshLoading } = usePlatformBI(
        canView ? fetchFreshness : null,
    );

    if (!authLoading && !canView) return null;

    const s = summary as any;
    const agencyList = Array.isArray(agencies) ? agencies : [];
    const bldgList = Array.isArray(buildings) ? buildings : [];
    const etlList = Array.isArray(etlHealth) ? etlHealth : [];
    const freshList = Array.isArray(freshness) ? freshness : [];
    const adoptData = adoption as any;

    return (
        <div className="space-y-8 pb-20">
            <PageHeader
                title="Platform Analytics"
                icon={<BarChart3 className="h-5 w-5" />}
                description={
                    `${s?.total_buildings ?? bldgList.length} buildings across all agencies. ` +
                    "Platform-wide ETL health, feature adoption, and cross-agency risk view."
                }
                badges={
                    <>
                        <Badge variant="secondary">Platform Administration</Badge>
                        <Badge variant="outline">Super Admin Only</Badge>
                    </>
                }
                actions={
                    <Button variant="outline" size="sm" onClick={refresh}>
                        <RefreshCw className="mr-2 h-4 w-4" /> Refresh
                    </Button>
                }
            />

            {/* Platform KPIs */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                {[
                    {
                        title: "Total Buildings",
                        value: s?.total_buildings ?? bldgList.length,
                        icon: Building2, tone: "default" as const, loading: sumLoading || bldgLoading,
                    },
                    {
                        title: "Total Levied",
                        value: s ? formatCurrency(s.total_levied) : "\u2014",
                        icon: BarChart3, tone: "default" as const, loading: sumLoading,
                    },
                    {
                        title: "Total Arrears",
                        value: s ? formatCurrency(s.total_arrears) : "\u2014",
                        icon: AlertTriangle,
                        tone: (s?.total_arrears ?? 0) > 0 ? ("critical" as const) : ("good" as const),
                        loading: sumLoading,
                    },
                    {
                        title: "BI Adoption",
                        value: adoptData ? `${adoptData.adoption_rate_pct ?? 0}%` : "\u2014",
                        icon: Zap, tone: "default" as const, loading: adoptLoading,
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
                {/* Agencies */}
                <Panel title="Agencies" loading={agencyLoading}>
                    <div className="max-h-64 overflow-y-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Agency</TableHead>
                                    <TableHead className="text-right">Buildings</TableHead>
                                    <TableHead className="text-right">Status</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {agencyList.map((a: any) => (
                                    <TableRow key={a.agency_id}>
                                        <TableCell>
                                            <span className="font-medium text-foreground">{a.name}</span>
                                            {a.is_self_managed && (
                                                <Badge variant="secondary" className="ml-1.5 text-[10px]">
                                                    Self-managed
                                                </Badge>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-right">{a.building_count}</TableCell>
                                        <TableCell className="text-right">
                                            <Badge variant={a.status === "active" ? "default" : "secondary"}>
                                                {a.status}
                                            </Badge>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                        {agencyList.length === 0 && (
                            <p className="py-8 text-center text-sm text-muted-foreground">
                                No agencies registered yet. Create agencies via the admin panel.
                            </p>
                        )}
                    </div>
                </Panel>

                {/* Buildings */}
                <Panel title="All Buildings" loading={bldgLoading}>
                    <div className="max-h-64 overflow-y-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Building</TableHead>
                                    <TableHead>Agency</TableHead>
                                    <TableHead className="text-right">Demo</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {bldgList.map((b: any) => (
                                    <TableRow key={b.scheme_id}>
                                        <TableCell>
                                            <span className="font-medium text-foreground">{b.name || b.building_id}</span>
                                            <span className="ml-1 text-muted-foreground">({b.building_id})</span>
                                        </TableCell>
                                        <TableCell className="text-muted-foreground">{b.agency_name || "\u2014"}</TableCell>
                                        <TableCell className="text-right">
                                            {b.is_demo ? (
                                                <span className="text-amber-700">Demo</span>
                                            ) : (
                                                <CheckCircle2 className="ml-auto h-3.5 w-3.5 text-emerald-600" aria-label="Live" />
                                            )}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                        {bldgList.length === 0 && (
                            <p className="py-8 text-center text-sm text-muted-foreground">No buildings found</p>
                        )}
                    </div>
                </Panel>

                {/* ETL Health */}
                <Panel title="ETL Health by Table" loading={etlLoading}>
                    <div className="max-h-64 overflow-y-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Table</TableHead>
                                    <TableHead className="text-right">Runs</TableHead>
                                    <TableHead className="text-right">Errors</TableHead>
                                    <TableHead className="text-right">Rows</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {etlList.map((row: any) => (
                                    <TableRow key={row.target_table}>
                                        <TableCell className="text-muted-foreground">{row.target_table.replace("analytics.", "")}</TableCell>
                                        <TableCell className="text-right">{row.total_runs}</TableCell>
                                        <TableCell className={`text-right font-medium ${row.error_runs > 0 ? "text-red-700" : "text-emerald-700"}`}>
                                            {row.error_runs}
                                        </TableCell>
                                        <TableCell className="text-right text-muted-foreground">
                                            {row.total_rows_inserted?.toLocaleString()}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                        {etlList.length === 0 && (
                            <p className="py-8 text-center text-sm text-muted-foreground">
                                No ETL runs yet. Trigger via POST /api/bi/admin/etl/run.
                            </p>
                        )}
                    </div>
                </Panel>

                {/* Feature adoption */}
                <Panel title="BI Feature Adoption" loading={adoptLoading}>
                    {adoptData ? (
                        <div className="space-y-4">
                            <div className="grid grid-cols-3 gap-3">
                                {[
                                    { label: "Total Buildings", value: adoptData.total_buildings },
                                    { label: "BI Enabled", value: adoptData.bi_enabled_count },
                                    { label: "PG Primary", value: adoptData.pg_primary_count },
                                ].map(({ label, value }) => (
                                    <div key={label} className="rounded-xl border border-border p-3 text-center">
                                        <p className="text-2xl font-semibold text-foreground">{value ?? 0}</p>
                                        <p className="mt-0.5 text-[11px] text-muted-foreground">{label}</p>
                                    </div>
                                ))}
                            </div>
                            <div>
                                <div className="mb-1 flex justify-between text-xs text-muted-foreground">
                                    <span>Adoption rate</span>
                                    <span className="font-semibold">{adoptData.adoption_rate_pct ?? 0}%</span>
                                </div>
                                <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                                    <div
                                        className="h-2 rounded-full bg-primary transition-all"
                                        style={{ width: `${adoptData.adoption_rate_pct ?? 0}%` }}
                                    />
                                </div>
                            </div>
                            {adoptData.adoption_rate_pct === 0 && (
                                <p className="text-xs text-muted-foreground">
                                    Enable{" "}
                                    <code className="rounded bg-muted px-1">bi_analytics_building_enabled</code>{" "}
                                    per building after ETL parity checks pass.
                                </p>
                            )}
                        </div>
                    ) : (
                        <p className="py-8 text-center text-sm text-muted-foreground">No adoption data</p>
                    )}
                </Panel>

                {/* Data freshness — stale items only */}
                <Panel title="Stale ETL Jobs (>48h)" loading={freshLoading}>
                    <div className="space-y-1">
                        {freshList
                            .filter((r: any) => r.status === "stale")
                            .slice(0, 15)
                            .map((r: any, i: number) => (
                                <div key={i} className="flex items-center justify-between rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-xs">
                                    <span className="text-muted-foreground">{r.target_table}</span>
                                    <div className="flex items-center gap-2 text-amber-700">
                                        <AlertTriangle className="h-3.5 w-3.5" />
                                        <span>{r.hours_since?.toFixed(0)}h ago</span>
                                    </div>
                                </div>
                            ))}
                        {freshList.filter((r: any) => r.status === "stale").length === 0 && (
                            <div className="flex h-20 items-center justify-center gap-2 text-sm text-emerald-700">
                                <CheckCircle2 className="h-4 w-4" />
                                All ETL jobs are fresh
                            </div>
                        )}
                    </div>
                </Panel>
            </div>
        </div>
    );
}
