"use client";

import {Suspense, useEffect, useState} from "react";
import {useSearchParams} from "next/navigation";
import Link from "next/link";
import {
    Activity,
    AlertTriangle,
    BarChart3,
    Calendar,
    ChevronRight,
    PieChart,
    RefreshCw,
    ShieldCheck,
    TrendingUp
} from "lucide-react";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Button} from "@/components/ui/button";
import {Progress} from "@/components/ui/progress";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from "@/components/ui/table";
import {Tabs, TabsContent, TabsList, TabsTrigger} from "@/components/ui/tabs";
import {useAuth} from "@/contexts/AuthContext";
import {formatMoneyFromDollars, formatMoneyCompact} from '@/lib/currency';
import {axisProps, barRadius, gridProps, seriesColor, tooltipProps} from "@/lib/chartTheme";
import {MetricHelp} from "@/components/shared/MetricHelp";
import {PageHeader} from "@/components/shared/PageHeader";
import {Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import {
    CapitalShockRisk,
    CapitalWorkItem,
    IntelligenceSummary,
    LevyFairnessResult,
    MaintenanceAttention,
    MaintenanceForecast
} from "@/types/intelligence";
/**
 * @generated FunctionHeader
 * Function: IntelligenceDashboard
 * Path: frontend/src/app/(app)/intelligence/building/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function IntelligenceDashboard() {
    const {api}: any = useAuth();
    const searchParams = useSearchParams();
    const [summary, setSummary] = useState<IntelligenceSummary | null>(null);
    const [forecast, setForecast] = useState<MaintenanceForecast | null>(null);
    const [capitalWorks, setCapitalWorks] = useState<CapitalWorkItem[]>([]);
    const [capitalShock, setCapitalShock] = useState<CapitalShockRisk | null>(null);
    const [levyFairness, setLevyFairness] = useState<LevyFairnessResult | null>(null);
    const [maintenanceRisks, setMaintenanceRisks] = useState<MaintenanceAttention[]>([]);
    const [sinkingFundPlan, setSinkingFundPlan] = useState<any>(null);
    const [levyStabilization, setLevyStabilization] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [recomputing, setRecomputing] = useState(false);
    const [activeTab, setActiveTab] = useState(searchParams?.get("tab") || "maintenance");

    useEffect(() => {
        fetchData();
    }, []);
    /**
     * @generated FunctionHeader
     * Function: fetchData
     * Path: frontend/src/app/(app)/intelligence/building/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fetchData = async () => {
        setLoading(true);
        try {
            const [summaryRes, forecastRes, capitalRes, shockRes, fairnessRes, risksRes, sfPlanRes, levySimRes] =
                await Promise.allSettled([
                    api.get("/intelligence/summary"),
                    api.get("/intelligence/maintenance-forecast"),
                    api.get("/intelligence/capital-works"),
                    api.get("/intelligence/capital-shock"),
                    api.get("/intelligence/levy-fairness"),
                    api.get("/intelligence/maintenance-risks"),
                    api.get("/finance/sinking-fund-plan"),
                    api.get("/intelligence/levy-stabilization"),
                ]);

            if (summaryRes.status === "fulfilled") setSummary(summaryRes.value.data);
            if (forecastRes.status === "fulfilled") setForecast(forecastRes.value.data);
            if (capitalRes.status === "fulfilled") setCapitalWorks(capitalRes.value.data);
            if (shockRes.status === "fulfilled") setCapitalShock(shockRes.value.data);
            if (fairnessRes.status === "fulfilled") setLevyFairness(fairnessRes.value.data);
            if (risksRes.status === "fulfilled") setMaintenanceRisks(risksRes.value.data || []);
            if (sfPlanRes.status === "fulfilled") setSinkingFundPlan(sfPlanRes.value.data);
            if (levySimRes.status === "fulfilled") setLevyStabilization(levySimRes.value.data);
        } catch (err) {
            console.error("Failed to fetch intelligence data", err);
        } finally {
            setLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleRecompute
     * Path: frontend/src/app/(app)/intelligence/building/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRecompute = async () => {
        setRecomputing(true);
        try {
            await api.post("/intelligence/recompute", {});
            await fetchData();
        } catch (err) {
            console.error("Recompute failed", err);
        } finally {
            setRecomputing(false);
        }
    };

    const attentionItem = summary?.attention_needed?.[0] || maintenanceRisks?.[0];
    const upcomingCapital = summary?.upcoming_capital_items?.[0] || capitalWorks?.[0];

    // The monthly series is now served, not synthesised here.
    //
    // This page used to spread one annual `predicted_cost` across twelve months using a
    // SEASONAL_WEIGHTS array invented in the frontend ("slightly higher in winter"). The
    // chart therefore looked modelled while nothing behind it was — a component
    // constructing financial truth. /intelligence/maintenance-forecast now returns
    // `monthly_breakdown` together with `monthly_basis`, which is either
    // `historical_seasonality` (measured from this building's own completed work orders)
    // or `even_spread` (not enough history — the annual figure divided by twelve, stated
    // as such rather than dressed up as a curve).
    const maintenanceChartData = forecast?.monthly_breakdown ?? [];
    const monthlyBasis = forecast?.monthly_basis;
    // Absent, not zero: an all-zero twelve months reads as a confident "no maintenance
    // expected next year", which is a much stronger claim than "we do not know yet".
    const hasMonthlyForecast = maintenanceChartData.length > 0;

    // TWO different reasons the chart can be empty, and they must not share a message.
    //
    // `monthly_breakdown` is only written when the intelligence engine recomputes. A
    // forecast doc stored before that field existed has a real `predicted_cost` and no
    // breakdown — telling that building "no costed assets" would be simply false, and
    // it is the state every building is in until its first recompute after deploy.
    const forecastPredates =
        !hasMonthlyForecast && (forecast?.predicted_cost ?? 0) > 0;

    // ── Actual spend history ─────────────────────────────────────────────────
    //
    // A flat twelve-month line was the SYMPTOM. Two things caused it, and only one
    // of them was the monthly split:
    //
    //   1. `monthly_basis` is `even_spread` for this building, because the shape is
    //      measured from completed WORK ORDERS and East Gate has 9 costed ones
    //      (threshold 12). There is no seasonal signal to show — and there is none
    //      in the GL either: every row in finance.expense_transactions is an ANNUAL
    //      total dated 31 December, one distinct date per financial year. Inventing
    //      a curve here would be the SEASONAL_WEIGHTS mistake again.
    //
    //   2. The annual figure being spread was itself wrong by ~6x. `predicted_cost`
    //      is an asset-risk model (2% of replacement cost x risk) with no link to
    //      what the building actually spends: $26,765/yr against real maintenance
    //      spend of $147k-$191k/yr.
    //
    // So the honest chart is the one the data supports: real spend per completed
    // year, which genuinely varies, next to both forecasts, each labelled with its
    // basis. Showing the two side by side is the point — the gap between them is
    // information, and averaging them would destroy it.
    const annualHistory = forecast?.annual_history ?? [];
    const hasAnnualHistory = annualHistory.length > 0;
    const historyForecast = forecast?.history_based_forecast ?? null;

    // Monthly seasonality is only shown when it was MEASURED. Otherwise the annual
    // view is the more truthful rendering of the same data.
    const showMonthlyChart = hasMonthlyForecast && monthlyBasis === "historical_seasonality";
    const showAnnualChart = !showMonthlyChart && hasAnnualHistory;

    const annualChartData = [
        ...annualHistory.map((h) => ({
            label: h.year,
            actual_cost: h.actual_cost,
            forecast_cost: null as number | null,
        })),
        ...(historyForecast !== null
            ? [{label: "Next 12m", actual_cost: null as number | null, forecast_cost: historyForecast}]
            : []),
    ];

    if (loading) return <div className="p-8 text-center">Loading Intelligence Engine...</div>;

    return (
        <div className="space-y-8">
            {/* Canonical page chrome. This page previously hand-rolled its header at
                text-3xl/font-bold while every migrated page uses PageHeader at text-2xl/
                font-semibold — which is why the section still looked unchanged after the
                colour-only pass. PageHeader also supplies the page's single <h1>. */}
            <PageHeader
                title="Building Intelligence"
                icon={<Activity className="h-5 w-5"/>}
                description="Predictive maintenance and long-term financial health analytics."
                actions={
                    <>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleRecompute}
                            disabled={recomputing}
                            className="flex items-center gap-2"
                        >
                            <RefreshCw className={`h-4 w-4 ${recomputing ? 'animate-spin' : ''}`}/>
                            {recomputing ? "Recomputing..." : "Sync Engine"}
                        </Button>
                        <Button variant="outline" size="sm" asChild>
                            <Link href="/intelligence/levy-fairness">Levy Fairness</Link>
                        </Button>
                        <Button variant="outline" size="sm" asChild>
                            <Link href="/intelligence/capital-risk">Capital Shock Risk</Link>
                        </Button>
                    </>
                }
            />

            {/* 3-Signal Pattern */}
            <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
                <Link href="/intelligence/assets" className="block h-full hover:opacity-90 transition-opacity">
                    <Card className="h-full flex flex-col cursor-pointer hover:shadow-md transition-shadow">
                        <CardHeader className="space-y-1">
                            <CardTitle className="text-sm font-medium flex items-center gap-2">
                                <AlertTriangle className="h-4 w-4 text-rose-500"/>
                                Attention Needed
                                <ChevronRight className="h-3 w-3 ml-auto text-muted-foreground"/>
                            </CardTitle>
                            <CardDescription>Highest-priority maintenance risk.</CardDescription>
                        </CardHeader>
                        <CardContent className="flex-1">
                            {attentionItem ? (
                                <div className="space-y-2">
                                    <div className="font-semibold">{attentionItem.asset_name}</div>
                                    <div className="text-sm text-muted-foreground">
                                        {attentionItem.repairs_last_12m} repairs •
                                        {formatMoneyFromDollars(attentionItem.repair_cost_last_12m)} last 12 months
                                    </div>
                                    <div className="text-sm text-rose-600">{attentionItem.recommendation}</div>
                                </div>
                            ) : (
                                <div className="text-sm text-muted-foreground">No urgent maintenance risks
                                    detected.</div>
                            )}
                        </CardContent>
                    </Card>
                </Link>

                <div className="cursor-pointer hover:opacity-90 transition-opacity"
                     onClick={() => setActiveTab("capital")}>
                    <Card className="h-full hover:shadow-md transition-shadow border-primary/20 hover:border-primary/20">
                        <CardHeader className="space-y-1">
                            <CardTitle className="text-sm font-medium flex items-center gap-2">
                                <Calendar className="h-4 w-4 text-primary"/>
                                Upcoming Capital Costs
                                <ChevronRight className="h-3 w-3 ml-auto text-muted-foreground"/>
                            </CardTitle>
                            <CardDescription>Next scheduled capital replacement.</CardDescription>
                        </CardHeader>
                        <CardContent className="flex-1">
                            {upcomingCapital ? (
                                <div className="space-y-2">
                                    <div className="font-semibold">{upcomingCapital.asset_name}</div>
                                    <div className="text-sm text-muted-foreground">
                                        {upcomingCapital.replacement_year} • {formatMoneyFromDollars(upcomingCapital.estimated_cost)}
                                    </div>
                                </div>
                            ) : (
                                <div className="text-sm text-muted-foreground">None scheduled</div>
                            )}
                        </CardContent>
                    </Card>
                </div>

                <Link href="/intelligence/capital-risk" className="block h-full hover:opacity-90 transition-opacity">
                    <Card
                        className="h-full flex flex-col cursor-pointer hover:shadow-md transition-shadow">
                        <CardHeader className="space-y-1">
                            <CardTitle className="text-sm font-medium flex items-center gap-2">
                                <PieChart className="h-4 w-4 text-amber-500"/>
                                Financial Stability
                                <ChevronRight className="h-3 w-3 ml-auto text-muted-foreground"/>
                            </CardTitle>
                            <CardDescription>Reserve resilience against shocks.</CardDescription>
                        </CardHeader>
                        <CardContent className="flex-1">
                            <div className="space-y-2">
                                {/* `|| "LOW"` used to mean an absent payload rendered as
                                    "Risk Level: LOW" — a reassuring claim made from no data.
                                    Unknown is now shown as unknown. */}
                                <div className="font-semibold">
                                    Risk Level: {capitalShock?.risk_level ?? "Unknown"}
                                </div>
                                {/* reserve_balance is null when the building has no financial
                                    baseline; the backend no longer substitutes a hardcoded
                                    $150,000. Render unavailable rather than a dollar figure. */}
                                <div className="text-sm text-muted-foreground">
                                    {capitalShock?.reserve_balance_available === false ||
                                     capitalShock?.reserve_balance == null
                                        ? "Reserve balance not recorded for this building yet"
                                        : `Reserve balance ${formatMoneyFromDollars(capitalShock.reserve_balance)}`}
                                </div>
                                {capitalShock?.recommendation && (
                                    <div
                                        className="text-sm text-amber-600 line-clamp-2">{capitalShock.recommendation}</div>
                                )}
                            </div>
                        </CardContent>
                    </Card>
                </Link>

                <Link href="/intelligence/capital-risk" className="block h-full hover:opacity-90 transition-opacity">
                    <Card className="h-full flex flex-col cursor-pointer hover:shadow-md transition-shadow">
                        <CardHeader className="space-y-1">
                            <CardTitle className="text-sm font-medium flex items-center gap-2">
                                <BarChart3 className="h-4 w-4 text-rose-500"/>
                                Capital Works Risk
                                <ChevronRight className="h-3 w-3 ml-auto text-muted-foreground"/>
                            </CardTitle>
                            <CardDescription>Forecast capital shock window.</CardDescription>
                        </CardHeader>
                        <CardContent className="flex-1">
                            {capitalShock?.capital_shock_index?.next_shock ? (
                                <div className="space-y-2">
                                    <div
                                        className="font-semibold">FY {capitalShock.capital_shock_index.next_shock.year}</div>
                                    <div className="text-sm text-muted-foreground">
                                        {formatMoneyFromDollars(capitalShock.capital_shock_index.next_shock.capital_spend)} •{" "}
                                        {capitalShock.capital_shock_index.next_shock.risk_level}
                                    </div>
                                </div>
                            ) : (
                                <div className="text-sm text-muted-foreground">No major shocks detected.</div>
                            )}
                        </CardContent>
                    </Card>
                </Link>
            </div>

            {/* KPI Stats */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                <Link href="/intelligence/assets" className="block">
                    <Card className="cursor-pointer hover:shadow-md transition-shadow">
                        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                            <CardTitle className="text-sm font-medium flex items-center">Asset Health Score<MetricHelp
                                text="Composite score (0–100) measuring the overall condition of all building assets. Calculated from age vs expected life, repair frequency, and service recency across all tracked assets."/></CardTitle>
                            <ShieldCheck className="h-4 w-4 text-emerald-500"/>
                        </CardHeader>
                        <CardContent className="flex-1">
                            {/* `|| 0` rendered an absent score as "0/100" with an amber bar —
                                i.e. a building with no data looked catastrophically unhealthy.
                                That is the same fabrication as the reserve-balance default, just
                                pointing the other way. Unknown is now shown as unknown. */}
                            {summary?.asset_health_score == null ? (
                                <>
                                    <div className="text-2xl font-bold text-muted-foreground">&mdash;/100</div>
                                    <p className="mt-2 text-xs text-muted-foreground">
                                        No asset condition data recorded yet
                                    </p>
                                </>
                            ) : (
                                <>
                                    <div className="text-2xl font-bold">{summary.asset_health_score}/100</div>
                                    <Progress
                                        value={summary.asset_health_score}
                                        className={`mt-2 [&>div]:${summary.asset_health_score > 70 ? 'bg-emerald-500' : 'bg-amber-500'}`}
                                    />
                                </>
                            )}
                        </CardContent>
                    </Card>
                </Link>

                <div className="cursor-pointer" onClick={() => setActiveTab("maintenance")}>
                    <Card className="hover:shadow-md transition-shadow">
                        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                            <CardTitle className="text-sm font-medium flex items-center">12m Maintenance
                                Forecast<MetricHelp
                                    text="Two independent estimates. HISTORY is the mean of this building's own actual maintenance spend over its last three completed financial years. ASSET MODEL is 2% of each asset's replacement cost scaled by its risk score. They answer different questions and are shown separately on purpose — a large gap between them means the asset register understates what the building actually spends, and averaging them would hide that."/></CardTitle>
                            <TrendingUp className="h-4 w-4 text-primary"/>
                        </CardHeader>
                        <CardContent className="flex-1">
                            {/* Lead with the history-anchored figure when it exists: it is
                                measured, and the asset model demonstrably is not calibrated
                                to real spend (East Gate: $26,765 modelled vs ~$164k actual).
                                Never average or silently substitute one for the other. */}
                            <div className="text-2xl font-bold">
                                {formatMoneyFromDollars(historyForecast ?? forecast?.predicted_cost ?? summary?.expected_maintenance_12m)}
                            </div>
                            {historyForecast !== null ? (
                                <p className="text-xs text-muted-foreground">
                                    From actual spend · asset model
                                    says {formatMoneyFromDollars(forecast?.predicted_cost ?? 0)}
                                </p>
                            ) : (
                                <p className="text-xs text-muted-foreground">
                                    Asset model · {forecast?.predicted_repairs_count || 0} repairs predicted
                                </p>
                            )}
                        </CardContent>
                    </Card>
                </div>

                <Link href="/intelligence/assets" className="block">
                    <Card className="cursor-pointer hover:shadow-md transition-shadow">
                        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                            <CardTitle className="text-sm font-medium flex items-center">High Risk Assets<MetricHelp
                                text="Number of assets with a risk score above 75, indicating high probability of failure or urgent maintenance need within the next 12 months."/></CardTitle>
                            <AlertTriangle className="h-4 w-4 text-rose-500"/>
                        </CardHeader>
                        <CardContent className="flex-1">
                            <div
                                className="text-2xl font-bold">{summary?.high_risk_assets_count || forecast?.high_risk_assets?.length || 0}</div>
                            <p className="text-xs text-muted-foreground">Requires attention</p>
                        </CardContent>
                    </Card>
                </Link>

                <Link href="/intelligence/capital-risk" className="block">
                    <Card className="cursor-pointer hover:shadow-md transition-shadow">
                        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                            <CardTitle className="text-sm font-medium flex items-center">5y Capital Need<MetricHelp
                                text="Total estimated cost of capital works and asset replacements scheduled in the next 5 years, based on the capital works plan and asset expected lifespans."/></CardTitle>
                            <Activity className="h-4 w-4 text-primary"/>
                        </CardHeader>
                        <CardContent className="flex-1">
                            <div className="text-2xl font-bold">{formatMoneyFromDollars(summary?.capital_replacement_5y)}</div>
                            <p className="text-xs text-muted-foreground">Sinking fund target</p>
                        </CardContent>
                    </Card>
                </Link>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
                <TabsList className="grid w-full grid-cols-3 lg:w-[400px]">
                    <TabsTrigger value="maintenance">Maintenance</TabsTrigger>
                    <TabsTrigger value="capital">Capital Works</TabsTrigger>
                    <TabsTrigger value="simulation">Levy Simulation</TabsTrigger>
                </TabsList>

                <TabsContent value="maintenance" className="space-y-4 pt-4">
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                        <Card className="lg:col-span-2">
                            <CardHeader>
                                <CardTitle>
                                    {showMonthlyChart ? "12-Month Predictive Maintenance" : "Maintenance Spend & Forecast"}
                                </CardTitle>
                                <CardDescription>
                                    {showMonthlyChart
                                        ? "Estimated cost by month, shaped by this building's own completed work-order history."
                                        : showAnnualChart
                                            ? `Actual maintenance spend per financial year, from this building's own category actuals.${
                                                forecast?.history_forecast_basis
                                                    ? ` Forecast is the ${forecast.history_forecast_basis}.`
                                                    : " Not enough completed years yet to project a forecast from history."
                                              } There is no month-level maintenance history for this building, so no monthly curve is shown rather than an invented one.`
                                            : "Estimated repair frequency and cost by month."}
                                </CardDescription>
                            </CardHeader>
                            <CardContent className="h-[300px]">
                                {showMonthlyChart ? (
                                    <ResponsiveContainer width="100%" height="100%">
                                        <AreaChart data={maintenanceChartData}>
                                            <CartesianGrid {...gridProps}/>
                                            <XAxis dataKey="label" {...axisProps}/>
                                            <YAxis {...axisProps}
                                                   tickFormatter={(v: number) => formatMoneyCompact(v)}/>
                                            <Tooltip {...tooltipProps}
                                                     formatter={(v: any) => [`${formatMoneyFromDollars(v)}`, "Est. Cost"]}/>
                                            <Area type="monotone" dataKey="predicted_cost"
                                                  stroke={seriesColor(0)} fill={seriesColor(0)}
                                                  fillOpacity={0.2}/>
                                        </AreaChart>
                                    </ResponsiveContainer>
                                ) : showAnnualChart ? (
                                    /* Two SEPARATE series, deliberately. Actual spend and a
                                       forecast are different kinds of claim, and a single
                                       series would let the eye read the projection as
                                       measured. Nulls keep each bar in its own column. */
                                    <ResponsiveContainer width="100%" height="100%">
                                        <BarChart data={annualChartData}>
                                            <CartesianGrid {...gridProps}/>
                                            <XAxis dataKey="label" {...axisProps}/>
                                            <YAxis {...axisProps}
                                                   tickFormatter={(v: number) => formatMoneyCompact(v)}/>
                                            <Tooltip {...tooltipProps}
                                                     formatter={(v: any, name: any) => [
                                                         v == null ? "—" : `${formatMoneyFromDollars(v)}`,
                                                         name === "actual_cost" ? "Actual spend" : "Forecast",
                                                     ]}/>
                                            <Bar dataKey="actual_cost" name="actual_cost"
                                                 fill={seriesColor(0)} radius={[4, 4, 0, 0]}/>
                                            <Bar dataKey="forecast_cost" name="forecast_cost"
                                                 fill={seriesColor(1)} fillOpacity={0.55}
                                                 radius={[4, 4, 0, 0]}/>
                                        </BarChart>
                                    </ResponsiveContainer>
                                ) : (
                                    <div
                                        className="flex h-full items-center justify-center px-6 text-center text-sm text-muted-foreground">
                                        {forecastPredates
                                            ? `A ${formatMoneyFromDollars(forecast?.predicted_cost ?? 0)} annual forecast is on file, but it was
                                               computed before the monthly breakdown existed. Run Sync Engine to produce it.`
                                            : "No maintenance forecast yet — the intelligence engine has no costed assets for this building. Add assets with a replacement cost, then run Sync Engine."}
                                    </div>
                                )}
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader>
                                <CardTitle>Attention Needed</CardTitle>
                                <CardDescription>Assets showing recurring repairs or replace-now signals.</CardDescription>
                            </CardHeader>
                            <CardContent className="flex-1">
                                <div className="space-y-4">
                                    {(summary?.attention_needed || maintenanceRisks)?.length ? (
                                        (summary?.attention_needed || maintenanceRisks).map((asset, i) => (
                                            <div key={i}
                                                 className="flex items-start justify-between border-b pb-2 last:border-0">
                                                <div>
                                                    <div className="font-medium text-sm">{asset.asset_name}</div>
                                                    <div className="text-xs text-muted-foreground">
                                                        {asset.repairs_last_12m} repairs •
                                                        {formatMoneyFromDollars(asset.repair_cost_last_12m)}
                                                    </div>
                                                </div>
                                                <div
                                                    className="text-xs px-2 py-1 bg-rose-100 text-rose-700 rounded-full">ACTION
                                                </div>
                                            </div>
                                        ))
                                    ) : (
                                        <p className="text-muted-foreground text-sm">No urgent maintenance risks
                                            detected.</p>
                                    )}
                                </div>
                            </CardContent>
                        </Card>
                    </div>
                </TabsContent>

                <TabsContent value="capital" className="space-y-4 pt-4">
                    {/* 15-Year Sinking Fund Plan */}
                    {sinkingFundPlan?.plan?.length > 0 && (
                        <Card>
                            <CardHeader>
                                <CardTitle>15-Year Sinking Fund Capital Works Plan (2021–2035)</CardTitle>
                                <CardDescription>
                                    Annual contributions vs. capital expenditure from the approved sinking fund plan.
                                    {sinkingFundPlan.summary?.total_contributions > 0 && (
                                        <span className="ml-1 text-emerald-600 font-medium">
                      Total contributions: {formatMoneyFromDollars(sinkingFundPlan.summary.total_contributions)} •
                      Projected closing balance (2035): {formatMoneyFromDollars(sinkingFundPlan.summary.closing_balance_2035)}
                    </span>
                                    )}
                                </CardDescription>
                            </CardHeader>
                            <CardContent className="flex-1">
                                <div className="h-[240px] mb-4">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <BarChart data={sinkingFundPlan.plan.filter((r: any) => r.year >= 2024)}>
                                            <CartesianGrid strokeDasharray="3 3" vertical={false}/>
                                            <XAxis dataKey="year"/>
                                            <YAxis tickFormatter={(v: number) => formatMoneyCompact(v)}/>
                                            <Tooltip formatter={(v: any) => [`${formatMoneyFromDollars(v)}`, ""]}/>
                                            <Bar dataKey="contribution" fill={seriesColor(0)} name="Contribution"
                                                 radius={barRadius}/>
                                            <Bar dataKey="expenditure" fill={seriesColor(2)} name="Expenditure"
                                                 radius={barRadius}/>
                                        </BarChart>
                                    </ResponsiveContainer>
                                </div>
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Year</TableHead>
                                            <TableHead className="text-right">Contribution</TableHead>
                                            <TableHead className="text-right">Expenditure</TableHead>
                                            <TableHead className="text-right">Closing Balance</TableHead>
                                            <TableHead>Status</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {sinkingFundPlan.plan.map((row: any) => (
                                            <TableRow key={row.year}
                                                      className={row.is_major_capital_year ? "bg-amber-50" : ""}>
                                                <TableCell className="font-bold">{row.year}</TableCell>
                                                <TableCell
                                                    className="text-right text-emerald-600">{formatMoneyFromDollars(row.contribution)}</TableCell>
                                                <TableCell
                                                    className="text-right text-amber-600">{formatMoneyFromDollars(row.expenditure)}</TableCell>
                                                <TableCell
                                                    className="text-right font-mono">{formatMoneyFromDollars(row.closing_balance)}</TableCell>
                                                <TableCell>
                                                    {row.is_major_capital_year && (
                                                        <span
                                                            className="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium">Major Works</span>
                                                    )}
                                                    {row.is_actual && (
                                                        <span
                                                            className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground">Actual</span>
                                                    )}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </CardContent>
                        </Card>
                    )}

                    {/* 10-Year Capital Replacement Schedule */}
                    <Card>
                        <CardHeader>
                            <CardTitle>10-Year Capital Replacement Schedule</CardTitle>
                            <CardDescription>Planned major works and their inflated replacement costs.</CardDescription>
                        </CardHeader>
                        <CardContent className="flex-1">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Year</TableHead>
                                        <TableHead>Asset Name</TableHead>
                                        <TableHead className="text-right">Estimated Cost (CPI Adj)</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {capitalWorks.map((item, i) => (
                                        <TableRow key={i}>
                                            <TableCell className="font-bold">{item.replacement_year}</TableCell>
                                            <TableCell>{item.asset_name}</TableCell>
                                            <TableCell className="text-right font-mono text-primary font-semibold">
                                                {formatMoneyFromDollars(item.estimated_cost)}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                    {capitalWorks.length === 0 && (
                                        <TableRow>
                                            <TableCell colSpan={3} className="text-center text-muted-foreground py-8">
                                                None scheduled. Seed data via Settings → Digital Twin.
                                            </TableCell>
                                        </TableRow>
                                    )}
                                </TableBody>
                            </Table>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="simulation" className="space-y-4 pt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle>Levy Stabilization Simulation</CardTitle>
                            <CardDescription>10-year projection showing smoothed levy path vs volatile (pay-as-you-go)
                                path.</CardDescription>
                        </CardHeader>
                        <CardContent className="flex-1">
                            {!levyStabilization ? (
                                <p className="text-sm text-muted-foreground py-4 text-center">No simulation data
                                    available. Try recomputing the intelligence engine.</p>
                            ) : levyStabilization.baseline_available === false ? (
                                /* The backend has always returned `baseline_available`, and this page
                                   ignored it until 2026-08-28. When it is false every projected figure
                                   derives from a $0 annual levy, so the chart is a row of zeros and the
                                   reserve line falls forever — which reads as a catastrophic funding gap
                                   rather than as "we could not find this building's levy budget".
                                   Missing and zero are different states; say which one this is. */
                                <div className="py-6 text-center space-y-2">
                                    <AlertTriangle className="h-5 w-5 text-amber-500 mx-auto"/>
                                    <p className="text-sm font-medium">No levy baseline recorded for this building</p>
                                    <p className="text-xs text-muted-foreground max-w-md mx-auto">
                                        A ten-year projection needs this building&apos;s proposed annual levy
                                        {levyStabilization.baseline_basis?.levy_year
                                            ? ` (looked for financial year ${levyStabilization.baseline_basis.levy_year})`
                                            : ""}. Without it every projected figure would be $0, so no
                                        projection is shown rather than one that looks like a funding crisis.
                                    </p>
                                </div>
                            ) : (
                                <div className="flex flex-col md:flex-row gap-8">
                                    <div className="w-full md:w-1/3 space-y-6 border-r pr-8">
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium">Levy Growth Limit</label>
                                            <div className="flex gap-2 items-center">
                                                <Progress value={5} className="h-2"/>
                                                <span className="text-sm font-bold">5%</span>
                                            </div>
                                        </div>
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium">Reserve Target</label>
                                            <div className="flex gap-2 items-center">
                                                <Progress value={100} className="h-2"/>
                                                <span className="text-sm font-bold">12 months</span>
                                            </div>
                                        </div>
                                        <div className="bg-primary/10 p-4 rounded-lg border border-primary/20">
                                            <h4 className="text-sm font-bold text-primary mb-1">Recommendation</h4>
                                            <p className="text-xs text-primary leading-relaxed">
                                                {levyStabilization.recommendation}
                                            </p>
                                            {levyStabilization.baseline_basis && (
                                                /* State what the projection is built on. Every bar below is
                                                   derived from these two figures, and an unlabelled curve
                                                   invites the reader to trust it more than it has earned. */
                                                <p className="text-[11px] text-primary/70 mt-2 leading-relaxed">
                                                    Based on the FY{levyStabilization.baseline_basis.levy_year} proposed
                                                    annual levy of {formatMoneyFromDollars(levyStabilization.baseline_basis.annual_levy_total)}
                                                    {" "}(admin {formatMoneyFromDollars(levyStabilization.baseline_basis.annual_admin_levy)})
                                                    and an opening reserve of {formatMoneyFromDollars(levyStabilization.baseline_basis.opening_reserve)}.
                                                </p>
                                            )}
                                        </div>
                                        <div className="space-y-1">
                                            {levyStabilization.projections?.slice(0, 5).map((p: any) => (
                                                <div key={p.year}
                                                     className="flex justify-between text-xs text-muted-foreground">
                                                    <span>{p.year}</span>
                                                    <span
                                                        className={`font-medium ${p.capital_expenditure > 0 ? 'text-amber-600' : ''}`}>
                            ${(p.levy_required / 1000).toFixed(0)}k
                                                        {p.capital_expenditure > 0 && ` (+$${(p.capital_expenditure / 1000).toFixed(0)}k cap)`}
                          </span>
                                                    <span
                                                        className={p.levy_increase_pct > 3 ? 'text-rose-600 font-bold' : 'text-green-600'}>
                            +{p.levy_increase_pct}%
                          </span>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                    <div className="flex-1 h-[300px]">
                                        <ResponsiveContainer width="100%" height="100%">
                                            <BarChart data={levyStabilization.projections?.map((p: any) => ({
                                                year: String(p.year),
                                                volatile: Math.round(p.operating_cost + p.capital_expenditure),
                                                smoothed: Math.round(p.levy_required),
                                            }))}>
                                                <CartesianGrid strokeDasharray="3 3" vertical={false}/>
                                                <XAxis dataKey="year"/>
                                                <YAxis tickFormatter={(v: any) => formatMoneyCompact(v)}/>
                                                <Tooltip formatter={(v: any) => `${formatMoneyFromDollars(v)}`}/>
                                                <Bar dataKey="volatile" fill={seriesColor(2)} name="Volatile Path"
                                                     radius={barRadius}/>
                                                <Bar dataKey="smoothed" fill={seriesColor(0)} name="Smoothed Path"
                                                     radius={barRadius}/>
                                            </BarChart>
                                        </ResponsiveContainer>
                                    </div>
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/intelligence/building/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    return (
        <Suspense>
            <IntelligenceDashboard/>
        </Suspense>
    );
}
