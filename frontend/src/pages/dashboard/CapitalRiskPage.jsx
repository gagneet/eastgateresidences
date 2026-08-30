// @featuretrace:capital-works-intelligence — Capital Shock Risk: flags upcoming capital works
//   whose cost concentration risks a sinking-fund shortfall or special-levy shock.
// Layer: frontend
// Data flow: this page → GET /intelligence/capital-shock, GET /finance/sinking-fund-plan,
//            GET /intelligence/capital-works → services/capital_shock_service.py (building-scoped).
// Related: frontend/src/pages/dashboard/CapitalWorksPlanner.jsx
//           backend/routers/intelligence.py
//           backend/services/capital_shock_service.py
"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../../contexts/AuthContext";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { PageHeader } from "../../components/shared/PageHeader";
import { AlertTriangle, ArrowLeft, BarChart3, Info, ShieldCheck, TrendingDown } from "lucide-react";
import {
    Area,
    AreaChart,
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    ReferenceLine,
    ResponsiveContainer,
    Tooltip as RechartsTooltip,
    XAxis,
    YAxis
} from "recharts";
import { formatNumber } from "../../lib/utils";
import { formatMoneyFromDollars } from '@/lib/currency';
import { MetricHelp as HelpTip } from "../../components/shared/MetricHelp";
// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: fmtNum
 * Path: frontend/src/pages/dashboard/CapitalRiskPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmtNum = (n, dp = 2) => formatNumber(n, dp);

const RISK_CONFIG = {
    severe: {
        color: "bg-rose-100 text-rose-700 border-rose-300",
        border: "border-rose-200",
        bar: "#f43f5e",
        label: "Severe"
    },
    major: {
        color: "bg-orange-100 text-orange-700 border-orange-300",
        border: "border-orange-200",
        bar: "#f97316",
        label: "Major"
    },
    moderate: {
        color: "bg-amber-100 text-amber-700 border-amber-300",
        border: "border-amber-200",
        bar: "#f59e0b",
        label: "Moderate"
    },
    low: {
        color: "bg-emerald-100 text-emerald-700 border-emerald-300",
        border: "border-emerald-200",
        bar: "#10b981",
        label: "Low"
    },
};
/**
 * @generated FunctionHeader
 * Function: riskConfig
 * Path: frontend/src/pages/dashboard/CapitalRiskPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const riskConfig = (level) =>
    RISK_CONFIG[ String(level || "").toLowerCase() ] || RISK_CONFIG.low;
// ─── Component ────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: CapitalRiskPage
 * Path: frontend/src/pages/dashboard/CapitalRiskPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CapitalRiskPage = () => {
    const router = useRouter();
    const {api} = useAuth();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        let mounted = true;
        api.get("/intelligence/capital-shock")
            .then((res) => {
                if (mounted) setData(res.data);
            })
            .catch((err) => {
                console.error("Failed to load capital shock data", err);
                if (mounted) setError("Failed to load data.");
            })
            .finally(() => {
                if (mounted) setLoading(false);
            });
        return () => {
            mounted = false;
        };
    }, [api]);

    if (loading) return (
        <div>
            <div className="animate-pulse space-y-4">
                {[...Array(3)].map((_, i) => (
                    <div key={i} className="h-32 bg-muted rounded-lg"/>
                ))}
            </div>
        </div>
    );

    if (error || !data) return (
        <div>
            <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4"/>
                <AlertDescription>{error || "No capital shock data available."}</AlertDescription>
            </Alert>
        </div>
    );

    const rc = riskConfig(data.risk_level);
    const reserveChartData = ( data.reserve_projection || [] ).map((row) => ( {
        year: String(row.year),
        before: row.reserve_before_capital,
        after: row.reserve_after_capital,
        capital: row.capital_cost,
    } ));
    const csiChartData = ( data.capital_shock_index?.rows || [] ).map((row) => ( {
        year: String(row.year),
        csi: row.capital_shock_index || 0,
        risk: String(row.risk_level || "").toLowerCase(),
    } ));

    return (
        <div className="space-y-6">
            {/* Header */}
            {/* Back affordance stays above the header: PageHeader owns title/description/
                actions, not navigation out of the page. */}
            <Button
                variant="ghost"
                size="sm"
                className="w-fit -ml-2 text-muted-foreground"
                onClick={() => router.push("/intelligence/building")}
            >
                <ArrowLeft className="h-4 w-4 mr-1"/>
                Back to Intelligence Hub
            </Button>
            <PageHeader
                title="Capital Shock Risk"
                icon={<ShieldCheck className="h-5 w-5"/>}
                description="Proactively identifies reserve shortfalls before major asset replacements occur, so the committee can take action before a special levy becomes necessary."
            />

            {/* Methodology info */}
            <Alert>
                <Info className="h-4 w-4"/>
                <AlertDescription className="text-xs leading-relaxed">
                    <strong>How this is calculated:</strong> The system compares the projected sinking fund reserve
                    balance
                    against scheduled capital works costs year-by-year. A <em>Capital Shock Index (CSI)</em> &gt; 1.5
                    indicates
                    a year where capital expenditure significantly exceeds the historical median, creating a reserve
                    shortfall risk.
                    The <em>Sinking Fund Adequacy Ratio (SFAR)</em> measures whether accumulated reserves plus future
                    levies
                    are sufficient to cover all planned capital works. SFAR &lt; 1.0 means the fund is underfunded.
                </AlertDescription>
            </Alert>

            {/* Summary cards */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <Card className={`border-2 ${rc.border}`}>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium flex items-center">
                            <AlertTriangle className="h-4 w-4 mr-2"/>
                            Overall Risk Level
                            <HelpTip
                                text="Derived from the worst-case capital shock year and SFAR score. Severe = reserve will be exhausted within 5 years without intervention."/>
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <Badge className={`text-sm px-3 py-1 ${rc.color}`}>{data.risk_level || "Low"}</Badge>
                        {data.recommendation && (
                            <p className="text-xs text-muted-foreground mt-2">{data.recommendation}</p>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium flex items-center">
                            <ShieldCheck className="h-4 w-4 mr-2 text-emerald-500"/>
                            Current Reserve Balance
                            <HelpTip
                                text="The current sinking fund balance available to cover capital works, from the latest annual levy record's sinking_fund.closing_balance. Shows an em dash when no balance has been recorded for this building — it is never substituted with a default."/>
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        {/* reserve_balance is null when the building has no financial
                            baseline. Render it as unavailable, never as a dollar figure and
                            never as $0.00 — the backend used to substitute a hardcoded
                            $150,000 here, which read as a real balance beside $0 income. */}
                        {data.reserve_balance_available === false || data.reserve_balance == null ? (
                            <>
                                <div className="text-2xl font-bold text-muted-foreground">&mdash;</div>
                                <p className="text-xs text-muted-foreground mt-1">
                                    No sinking-fund balance recorded for this building yet
                                </p>
                            </>
                        ) : (
                            <>
                                <div className="text-2xl font-bold">{formatMoneyFromDollars(data.reserve_balance)}</div>
                                <p className="text-xs text-muted-foreground mt-1">Available for capital works</p>
                            </>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium flex items-center">
                            <TrendingDown className="h-4 w-4 mr-2 text-primary"/>
                            Funding Adequacy (SFAR)
                            <HelpTip
                                text="Sinking Fund Adequacy Ratio = (Current Reserve + PV of Future Levies) ÷ (PV of Planned Capital Works). ≥1.0 is adequate; <1.0 means a potential shortfall."/>
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">
                            {data.funding_adequacy?.sfar != null ? fmtNum(data.funding_adequacy.sfar, 2) : "—"}
                        </div>
                        <p className="text-xs text-muted-foreground mt-1">{data.funding_adequacy?.status_label || "N/A"}</p>
                    </CardContent>
                </Card>
            </div>

            {/* Reserve Projection Chart */}
            {reserveChartData.length > 0 && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center">
                            <BarChart3 className="h-4 w-4 mr-2 text-primary"/>
                            10-Year Reserve Projection
                            <HelpTip
                                text="Shows the sinking fund balance before and after each year's capital expenditure. Red bars indicate years where capital costs exceed reserves, creating a shortfall."/>
                        </CardTitle>
                        <CardDescription>
                            Reserve balance trajectory with scheduled capital works deductions. Negative 'after' values
                            indicate reserve shortfalls.
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="h-64">
                            <ResponsiveContainer width="100%" height="100%">
                                <AreaChart data={reserveChartData} margin={{top: 4, right: 16, left: 8, bottom: 4}}>
                                    <defs>
                                        <linearGradient id="beforeGrad" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3}/>
                                            <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.05}/>
                                        </linearGradient>
                                        <linearGradient id="afterGrad" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="5%" stopColor="#10b981" stopOpacity={0.3}/>
                                            <stop offset="95%" stopColor="#10b981" stopOpacity={0.05}/>
                                        </linearGradient>
                                    </defs>
                                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9"/>
                                    <XAxis dataKey="year" tick={{fontSize: 11}}/>
                                    <YAxis tickFormatter={(v) => `$${( v / 1000 ).toFixed(0)}k`} tick={{fontSize: 11}}/>
                                    <RechartsTooltip
                                        formatter={(value, name) => [`${formatMoneyFromDollars(value)}`, name === "before" ? "Before Capital" : name === "after" ? "After Capital" : "Capital Cost"]}
                                    />
                                    <ReferenceLine y={0} stroke="#ef4444" strokeDasharray="3 3"/>
                                    <Area type="monotone" dataKey="before" stroke="#3b82f6" fill="url(#beforeGrad)"
                                          name="before"/>
                                    <Area type="monotone" dataKey="after" stroke="#10b981" fill="url(#afterGrad)"
                                          name="after"/>
                                </AreaChart>
                            </ResponsiveContainer>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Capital Shock Index */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center">
                        Capital Shock Index (CSI)
                        <HelpTip
                            text="CSI = Year's Capital Spend ÷ Median Historical Capital Spend. CSI >1.5 is 'Major', >2.5 is 'Severe'. A high CSI year needs advance planning to avoid reserve depletion."/>
                    </CardTitle>
                    <CardDescription>
                        Baseline median spend: <strong>{formatMoneyFromDollars(data.capital_shock_index?.baseline_spend)}</strong>
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    {csiChartData.length > 0 && (
                        <div className="h-48">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={csiChartData} margin={{top: 4, right: 16, left: 0, bottom: 4}}>
                                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9"/>
                                    <XAxis dataKey="year" tick={{fontSize: 11}}/>
                                    <YAxis tick={{fontSize: 11}}/>
                                    <RechartsTooltip formatter={(value) => [fmtNum(value, 2), "CSI"]}/>
                                    <ReferenceLine y={1.5} stroke="#f59e0b" strokeDasharray="4 4"
                                                   label={{value: "1.5 (Major)", position: "right", fontSize: 10}}/>
                                    <ReferenceLine y={2.5} stroke="#ef4444" strokeDasharray="4 4"
                                                   label={{value: "2.5 (Severe)", position: "right", fontSize: 10}}/>
                                    <Bar dataKey="csi" name="CSI" radius={[2, 2, 0, 0]}>
                                        {csiChartData.map((entry, i) => (
                                            <Cell key={i} fill={riskConfig(entry.risk).bar}/>
                                        ))}
                                    </Bar>
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    )}
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Year</TableHead>
                                <TableHead className="text-right">Capital Spend</TableHead>
                                <TableHead className="text-right">
                  <span className="inline-flex items-center">
                    CSI
                    <HelpTip text="Capital Shock Index for this year. Values above 1.5 warrant review."/>
                  </span>
                                </TableHead>
                                <TableHead className="text-right">Risk</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {data.capital_shock_index?.rows?.length ? data.capital_shock_index.rows.map((row) => (
                                <TableRow key={row.year}>
                                    <TableCell className="font-medium">{row.year}</TableCell>
                                    <TableCell className="text-right">{formatMoneyFromDollars(row.capital_spend)}</TableCell>
                                    <TableCell className="text-right">{fmtNum(row.capital_shock_index, 2)}</TableCell>
                                    <TableCell className="text-right">
                                        <Badge className={`text-[10px] ${riskConfig(row.risk_level).color}`}>
                                            {row.risk_level}
                                        </Badge>
                                    </TableCell>
                                </TableRow>
                            )) : (
                                <TableRow>
                                    <TableCell colSpan={4} className="text-center text-muted-foreground py-6">
                                        No CSI data available.
                                    </TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            {/* Funding Adequacy */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center">
                        Funding Adequacy
                        <HelpTip
                            text="SFAR ≥ 1.25 = Excellent (well-funded buffer). 1.0–1.25 = Adequate. 0.75–1.0 = At Risk (may need levy increase). <0.75 = Critical (special levy likely)."/>
                    </CardTitle>
                    <CardDescription>
                        How well the sinking fund covers projected capital works over the planning horizon.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <div className="bg-muted rounded-lg p-3">
                            <div className="text-xs text-muted-foreground mb-1">SFAR Score</div>
                            <div className="text-xl font-bold">
                                {data.funding_adequacy?.sfar != null ? fmtNum(data.funding_adequacy.sfar, 2) : "—"}
                            </div>
                        </div>
                        <div className="bg-muted rounded-lg p-3">
                            <div className="text-xs text-muted-foreground mb-1">Status</div>
                            <div className="text-sm font-medium">{data.funding_adequacy?.status_label || "N/A"}</div>
                        </div>
                        <div className="bg-muted rounded-lg p-3">
                            <div className="text-xs text-muted-foreground mb-1">Funding Gap</div>
                            <div className="text-sm font-medium text-rose-600">
                                {formatMoneyFromDollars(data.funding_adequacy?.funding_gap)}
                            </div>
                        </div>
                        <div className="bg-muted rounded-lg p-3">
                            <div className="text-xs text-muted-foreground mb-1">Reserve Collapse</div>
                            <div className="text-sm font-medium">
                                {data.reserve_collapse?.collapse_year || "Not predicted"}
                            </div>
                            <div className="text-xs text-muted-foreground">{data.reserve_collapse?.risk_label}</div>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* Shock Events */}
            {data.risks?.length > 0 && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center">
                            <AlertTriangle className="h-4 w-4 mr-2 text-rose-500"/>
                            Projected Shock Events
                            <HelpTip
                                text="Years where the sinking fund reserve is projected to fall below zero after capital expenditure. The deficit is the funding shortfall for that year."/>
                        </CardTitle>
                        <CardDescription>Years where reserve balance falls below capital expenditure
                            requirements.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Year</TableHead>
                                    <TableHead className="text-right">Projected Reserve</TableHead>
                                    <TableHead className="text-right">Deficit</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {data.risks.map((risk) => (
                                    <TableRow key={risk.year} className="bg-rose-50/50">
                                        <TableCell className="font-medium">{risk.year}</TableCell>
                                        <TableCell className="text-right">{formatMoneyFromDollars(risk.projected_reserve)}</TableCell>
                                        <TableCell
                                            className="text-right text-rose-600 font-medium">{formatMoneyFromDollars(risk.deficit)}</TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
            )}

            {/* Reserve Projection Table */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center">
                        Detailed Reserve Projection
                        <HelpTip
                            text="Year-by-year sinking fund projection. 'Before Capital' = opening reserve + levy contributions. 'Capital Cost' = scheduled replacements for that year. 'After Capital' = net reserve (can be negative = shortfall)."/>
                    </CardTitle>
                    <CardDescription>
                        10-year reserve trajectory with all scheduled capital works deducted.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Year</TableHead>
                                <TableHead className="text-right">Before Capital</TableHead>
                                <TableHead className="text-right">Capital Cost</TableHead>
                                <TableHead className="text-right">After Capital</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {data.reserve_projection?.length ? data.reserve_projection.map((row) => (
                                <TableRow key={row.year}
                                          className={row.reserve_after_capital < 0 ? "bg-rose-50/50" : ""}>
                                    <TableCell className="font-medium">{row.year}</TableCell>
                                    <TableCell className="text-right">{formatMoneyFromDollars(row.reserve_before_capital)}</TableCell>
                                    <TableCell className="text-right">{formatMoneyFromDollars(row.capital_cost)}</TableCell>
                                    <TableCell
                                        className={`text-right font-medium ${row.reserve_after_capital < 0 ? "text-rose-600" : "text-emerald-600"}`}>
                                        {formatMoneyFromDollars(row.reserve_after_capital)}
                                    </TableCell>
                                </TableRow>
                            )) : (
                                <TableRow>
                                    <TableCell colSpan={4} className="text-center text-muted-foreground py-6">No reserve
                                        projection data.</TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            {/* Action links */}
            <div className="flex gap-3">
                <Button variant="outline" onClick={() => router.push("/intelligence/capital-planner")}>
                    View Capital Works Planner →
                </Button>
                <Button variant="outline" onClick={() => router.push("/intelligence/financial")}>
                    View Full Finance Intelligence →
                </Button>
            </div>
        </div>
    );
};

export default CapitalRiskPage;
