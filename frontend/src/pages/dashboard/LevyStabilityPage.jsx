// @featuretrace:levy-stability — Levy Stability Score: year-over-year levy volatility scoring
//   with a what-if recompute simulator.
// Layer: frontend
// Data flow: this page → GET /intelligence/levy-stability, POST
//            /intelligence/levy-stability(/recompute,/what-if) →
//            services/levy_stability_service.py (building-scoped).
// Related: backend/routers/intelligence.py
//           backend/services/levy_stability_service.py
"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { toast } from "sonner";
import {
    Activity,
    AlertTriangle,
    ArrowLeft,
    BarChart3,
    Building2,
    CheckCircle,
    DollarSign,
    HelpCircle,
    Info,
    RefreshCw,
    Shield,
    SlidersHorizontal,
    Target,
    TrendingUp,
    XCircle,
    Zap
} from "lucide-react";
import { useAuth } from "../../contexts/AuthContext";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Label } from "../../components/ui/label";
import { Separator } from "../../components/ui/separator";
import { Slider } from "../../components/ui/slider";
import { Input } from "../../components/ui/input";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger, } from "../../components/ui/tooltip";
import {PageHeader} from "../../components/shared/PageHeader";
import {
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    PolarAngleAxis,
    PolarGrid,
    Radar,
    RadarChart,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis,
} from "recharts";

// ── Constants ──────────────────────────────────────────────────────────────────

const MANAGER_ROLES = ["super_admin", "strata_manager", "ec_member"];
// ── Helpers ────────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: aud
 * Path: frontend/src/pages/dashboard/LevyStabilityPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const aud = (n) =>
    n == null ? "—" : new Intl.NumberFormat("en-AU", {
        style: "currency",
        currency: "AUD",
        maximumFractionDigits: 0
    }).format(n);
/**
 * @generated FunctionHeader
 * Function: pct
 * Path: frontend/src/pages/dashboard/LevyStabilityPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const pct = (n, decimals = 1) =>
    n == null ? "—" : `${n > 0 ? "+" : ""}${Number(n).toFixed(decimals)}%`;
/**
 * @generated FunctionHeader
 * Function: lssBand
 * Path: frontend/src/pages/dashboard/LevyStabilityPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function lssBand(score) {
    if (score >= 85) return {
        label: "Stable",
        color: "text-emerald-600",
        bg: "bg-emerald-50 border-emerald-200",
        ring: "border-emerald-400",
        icon: CheckCircle
    };
    if (score >= 70) return {
        label: "Moderate",
        color: "text-primary",
        bg: "bg-primary/10 border-primary/20",
        ring: "border-primary/20",
        icon: CheckCircle
    };
    if (score >= 55) return {
        label: "Caution",
        color: "text-amber-600",
        bg: "bg-amber-50 border-amber-200",
        ring: "border-amber-400",
        icon: AlertTriangle
    };
    if (score >= 40) return {
        label: "At Risk",
        color: "text-orange-600",
        bg: "bg-orange-50 border-orange-200",
        ring: "border-orange-400",
        icon: AlertTriangle
    };
    return {
        label: "Critical",
        color: "text-rose-600",
        bg: "bg-rose-50 border-rose-200",
        ring: "border-rose-500",
        icon: XCircle
    };
}
/**
 * @generated FunctionHeader
 * Function: componentInfo
 * Path: frontend/src/pages/dashboard/LevyStabilityPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function componentInfo(key) {
    const MAP = {
        reserve_score: {
            label: "Reserve Adequacy",
            weight: "35%",
            icon: Shield,
            desc: "Ratio of current reserve balance to total projected capital requirements. Higher = more cushion."
        },
        shock_score: {
            label: "Capital Shock Risk",
            weight: "30%",
            icon: Zap,
            desc: "Probability of a one-off special levy within the projection horizon. Lower probability = higher score."
        },
        volatility_score: {
            label: "Levy Volatility",
            weight: "20%",
            icon: Activity,
            desc: "Coefficient of variation in per-unit levy distribution. Lower volatility = more predictable levies."
        },
        funding_score: {
            label: "Funding Sufficiency",
            weight: "15%",
            icon: DollarSign,
            desc: "Ratio of projected contributions (with inflation) to total capital required. ≥ 1.2 = fully funded."
        },
    };
    return MAP[ key ] || {label: key, weight: "?", icon: Info, desc: ""};
}
// ── Sub-components ─────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: LssGauge
 * Path: frontend/src/pages/dashboard/LevyStabilityPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function LssGauge({score, label = "Levy Stability Score", size = "lg", subtitle}) {
    const band = lssBand(score ?? 0);
    const BandIcon = band.icon;
    const isLarge = size === "lg";
    return (
        <div className={`rounded-2xl border-2 p-6 ${band.bg} flex flex-col items-center gap-3`}>
            <div
                className={`relative flex-shrink-0 ${isLarge ? "w-40 h-40" : "w-24 h-24"} rounded-full border-8 ${band.ring} flex items-center justify-center shadow-sm`}>
                <div className="text-center">
                    <div className={`font-semibold ${band.color}`} style={{fontSize: isLarge ? "3rem" : "1.6rem"}}>
                        {score ?? "—"}
                    </div>
                    <div className="text-xs text-muted-foreground">/100</div>
                </div>
            </div>
            <div className="text-center">
                <div
                    className={`font-bold ${isLarge ? "text-base" : "text-xs"} ${band.color} flex items-center gap-1 justify-center`}>
                    <BandIcon className={isLarge ? "w-4 h-4" : "w-3 h-3"}/>
                    {band.label}
                </div>
                <div className={`text-muted-foreground ${isLarge ? "text-sm" : "text-xs"} mt-0.5`}>{label}</div>
                {subtitle && <div className="text-xs text-muted-foreground mt-1 italic">{subtitle}</div>}
            </div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: ComponentCard
 * Path: frontend/src/pages/dashboard/LevyStabilityPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ComponentCard({scoreKey, value, isWhatIf, baseValue}) {
    const info = componentInfo(scoreKey);
    const Icon = info.icon;
    const band = lssBand(value ?? 0);
    const delta = isWhatIf && baseValue != null ? ( value ?? 0 ) - baseValue : null;
    return (
        <Card className={`${band.bg} border`}>
            <CardContent className="pt-4 space-y-2">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <Icon className={`w-4 h-4 ${band.color}`}/>
                        <span className="text-sm font-semibold">{info.label}</span>
                        <TooltipProvider>
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <HelpCircle className="w-3.5 h-3.5 text-muted-foreground cursor-help"/>
                                </TooltipTrigger>
                                <TooltipContent className="max-w-xs text-xs">{info.desc}</TooltipContent>
                            </Tooltip>
                        </TooltipProvider>
                    </div>
                    <Badge variant="outline" className="text-xs text-muted-foreground">{info.weight} weight</Badge>
                </div>
                <div className="flex items-end gap-2">
                    <div className={`text-3xl font-semibold ${band.color}`}>{value ?? "—"}</div>
                    <div className="text-muted-foreground text-sm mb-1">/100</div>
                    {delta != null && (
                        <div
                            className={`text-sm font-bold ml-auto ${delta > 0 ? "text-emerald-600" : delta < 0 ? "text-rose-600" : "text-muted-foreground"}`}>
                            {delta > 0 ? "+" : ""}{delta.toFixed(1)} vs baseline
                        </div>
                    )}
                </div>
                <div className="w-full bg-muted rounded-full h-2">
                    <div
                        className={`h-2 rounded-full ${
                            ( value ?? 0 ) >= 85 ? "bg-emerald-500" :
                                ( value ?? 0 ) >= 70 ? "bg-primary" :
                                    ( value ?? 0 ) >= 55 ? "bg-amber-500" :
                                        ( value ?? 0 ) >= 40 ? "bg-orange-500" : "bg-rose-500"
                        }`}
                        style={{width: `${value ?? 0}%`}}
                    />
                </div>
                <div className="text-xs text-muted-foreground">{band.label}</div>
            </CardContent>
        </Card>
    );
}
/**
 * @generated FunctionHeader
 * Function: GroupScoreCard
 * Path: frontend/src/pages/dashboard/LevyStabilityPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function GroupScoreCard({group}) {
    const band = lssBand(group.levy_stability_score ?? 0);
    return (
        <Card className="border">
            <CardContent className="pt-4">
                <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                        <Building2 className="w-4 h-4 text-muted-foreground"/>
                        <span className="font-semibold text-sm">{group.group}s</span>
                    </div>
                    <span className={`text-2xl font-semibold ${band.color}`}>{group.levy_stability_score}</span>
                </div>
                <div className="grid grid-cols-2 gap-2 text-xs">
                    {[
                        {key: "reserve_score", label: "Reserve"},
                        {key: "shock_score", label: "Shock Risk"},
                        {key: "volatility_score", label: "Volatility"},
                        {key: "funding_score", label: "Funding"},
                    ].map(({key, label}) => {
                        const v = group[ key ];
                        return (
                            <div key={key} className="flex justify-between">
                                <span className="text-muted-foreground">{label}:</span>
                                <span className="font-semibold">{v ?? "—"}</span>
                            </div>
                        );
                    })}
                </div>
            </CardContent>
        </Card>
    );
}
// ── Main Page ──────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: LevyStabilityPage
 * Path: frontend/src/pages/dashboard/LevyStabilityPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function LevyStabilityPage() {
    const {api, user} = useAuth();
    const router = useRouter();

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [recomputing, setRecomputing] = useState(false);
    const [whatIfResult, setWhatIfResult] = useState(null);
    const [whatIfLoading, setWhatIfLoading] = useState(false);

    // What-if scenario state
    const [sinkingIncrease, setSinkingIncrease] = useState(0);   // $/unit/quarter
    const [deferYears, setDeferYears] = useState(0);   // years to defer capital
    const [loanAmount, setLoanAmount] = useState(0);   // $ loan
    const [loanRate, setLoanRate] = useState(5);   // % interest
    const [loanTerm, setLoanTerm] = useState(10);  // years

    const canManage = MANAGER_ROLES.includes(user?.role) || user?.is_elevated || !!user?.temp_elevation;

    // ── Load ──────────────────────────────────────────────────────────────────────
    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await api.get("/intelligence/levy-stability");
            setData(res.data);
        } catch (err) {
            toast.error("Failed to load Levy Stability data");
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        load();
    }, [load]);
    // ── Recompute ────────────────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handleRecompute
     * Path: frontend/src/pages/dashboard/LevyStabilityPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRecompute = async () => {
        setRecomputing(true);
        try {
            const res = await api.post("/intelligence/levy-stability/recompute");
            setData(res.data);
            setWhatIfResult(null);
            toast.success("Levy Stability Score recomputed");
        } catch {
            toast.error("Recompute failed");
        } finally {
            setRecomputing(false);
        }
    };
    // ── What-If ──────────────────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handleWhatIf
     * Path: frontend/src/pages/dashboard/LevyStabilityPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleWhatIf = async () => {
        setWhatIfLoading(true);
        try {
            const payload = {
                sinking_fund_increase_per_unit: sinkingIncrease,
                defer_capital_years: deferYears,
                ...( loanAmount > 0 ? {
                    loan_amount: loanAmount,
                    loan_interest_rate: loanRate / 100,
                    loan_term_years: loanTerm,
                } : {} ),
            };
            const res = await api.post("/intelligence/levy-stability/what-if", payload);
            setWhatIfResult(res.data);
            toast.success("What-if scenario computed");
        } catch {
            toast.error("What-if scenario failed");
        } finally {
            setWhatIfLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: resetWhatIf
     * Path: frontend/src/pages/dashboard/LevyStabilityPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const resetWhatIf = () => {
        setWhatIfResult(null);
        setSinkingIncrease(0);
        setDeferYears(0);
        setLoanAmount(0);
    };

    // ── Loading state ──────────────────────────────────────────────────────────
    if (loading) {
        return (
            <div className="space-y-4">
                {[...Array(4)].map((_, i) => (
                    <div key={i} className={`h-${i === 0 ? 40 : 24} rounded-xl bg-muted animate-pulse`}/>
                ))}
            </div>
        );
    }

    if (!data) {
        return (
            <div>
                <Card className="text-center py-20">
                    <CardContent>
                        <Shield className="w-14 h-14 text-muted-foreground mx-auto mb-4"/>
                        <h2 className="text-xl font-semibold mb-2">Levy Stability Score Not Available</h2>
                        <p className="text-muted-foreground mb-6 text-sm">
                            The analysis needs to be computed first. It draws from capital shock, reserve balance, and
                            sinking fund data.
                        </p>
                        {canManage && (
                            <Button onClick={handleRecompute} disabled={recomputing}>
                                <RefreshCw className={`w-4 h-4 mr-2 ${recomputing ? "animate-spin" : ""}`}/>
                                {recomputing ? "Computing…" : "Generate Stability Score"}
                            </Button>
                        )}
                    </CardContent>
                </Card>
            </div>
        );
    }

    const activeData = whatIfResult || data;
    const lss = activeData.levy_stability_score ?? 0;
    const baseLss = data.levy_stability_score ?? 0;
    const band = lssBand(lss);
    const horizon = activeData.horizon_years ?? 10;
    const explanation = activeData.explanation ?? "";
    const components = activeData.components ?? {};
    const groupScores = activeData.group_scores ?? [];
    const generatedAt = activeData.generated_at ?? "";
    const isWhatIf = !!whatIfResult;

    // Build radar chart data
    const radarData = [
        {
            subject: "Reserve\nAdequacy",
            baseline: data.reserve_score ?? 0,
            whatif: whatIfResult?.reserve_score ?? data.reserve_score ?? 0
        },
        {
            subject: "Capital\nShock",
            baseline: data.shock_score ?? 0,
            whatif: whatIfResult?.shock_score ?? data.shock_score ?? 0
        },
        {
            subject: "Levy\nVolatility",
            baseline: data.volatility_score ?? 0,
            whatif: whatIfResult?.volatility_score ?? data.volatility_score ?? 0
        },
        {
            subject: "Funding\nSufficiency",
            baseline: data.funding_score ?? 0,
            whatif: whatIfResult?.funding_score ?? data.funding_score ?? 0
        },
    ];

    // Build group comparison bar data
    const groupBarData = groupScores.map(g => ( {
        name: `${g.group}s`,
        score: g.levy_stability_score,
    } ));

    return (
        <div className="space-y-6">
            {/* Back affordance sits above the header; PageHeader owns title, description
                and the action row, so this page's header matches every other migrated page
                including its bottom rule. */}
            <Button
                variant="ghost" size="sm" className="w-fit -ml-2 text-muted-foreground"
                onClick={() => router.push("/intelligence/building")}
            >
                <ArrowLeft className="h-4 w-4 mr-1"/> Back to Intelligence Hub
            </Button>
            <PageHeader
                title="Levy Stability Score Engine"
                icon={<Shield className="h-5 w-5"/>}
                description={`Composite score (0–100) predicting levy stability over ${horizon} years — Reserve Adequacy, Capital Shock Risk, Levy Volatility, Funding Sufficiency.`}
                actions={
                    <>
                        {isWhatIf && (
                            <Button variant="outline" size="sm" onClick={resetWhatIf}>
                                Reset to Baseline
                            </Button>
                        )}
                        <Button variant="outline" size="sm" onClick={load}>
                            <RefreshCw className="w-4 h-4 mr-2"/> Refresh
                        </Button>
                        {canManage && (
                            <Button size="sm" onClick={handleRecompute} disabled={recomputing}>
                                <RefreshCw className={`w-4 h-4 mr-2 ${recomputing ? "animate-spin" : ""}`}/>
                                {recomputing ? "Computing…" : "Recompute"}
                            </Button>
                        )}
                    </>
                }
            />

            {isWhatIf && (
                <motion.div initial={{opacity: 0, y: -8}} animate={{opacity: 1, y: 0}}
                            className="rounded-xl bg-primary/10 border border-primary/20 px-4 py-3 flex items-center gap-3 text-sm text-primary">
                    <Info className="w-4 h-4 flex-shrink-0"/>
                    <span>
            <strong>What-If Mode:</strong> Showing simulated scenario results.
            Sinking increase: <strong>{aud(sinkingIncrease)}/unit/qtr</strong>,
            Capital deferral: <strong>{deferYears}yr{deferYears !== 1 ? "s" : ""}</strong>
                        {loanAmount > 0 && `, Loan: ${aud(loanAmount)} @ ${loanRate}% over ${loanTerm}yr`}.
          </span>
                </motion.div>
            )}

            {/* Top score row */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                {/* Main gauge */}
                <div className="md:col-span-1 flex justify-center">
                    <LssGauge
                        score={lss}
                        label={isWhatIf ? "Simulated LSS" : "Levy Stability Score"}
                        subtitle={isWhatIf ? `Baseline: ${baseLss}` : `Computed ${generatedAt ? new Date(generatedAt).toLocaleDateString("en-AU") : "—"}`}
                    />
                </div>
                {/* Component KPI cards */}
                <div className="md:col-span-3 grid grid-cols-2 sm:grid-cols-4 gap-3">
                    {[
                        {key: "reserve_score", label: "Reserve\nAdequacy", value: activeData.reserve_score},
                        {key: "shock_score", label: "Capital\nShock", value: activeData.shock_score},
                        {key: "volatility_score", label: "Levy\nVolatility", value: activeData.volatility_score},
                        {key: "funding_score", label: "Funding\nSufficiency", value: activeData.funding_score},
                    ].map(({key, label, value}) => {
                        const b = lssBand(value ?? 0);
                        return (
                            <div key={key} className={`rounded-xl border p-4 ${b.bg}`}>
                                <p className="text-xs font-medium text-muted-foreground uppercase leading-tight">{label}</p>
                                <div className={`text-3xl font-semibold mt-1 ${b.color}`}>{value ?? "—"}</div>
                                <p className="text-xs text-muted-foreground mt-0.5">{b.label}</p>
                                {isWhatIf && data[ key ] != null && (
                                    <p className={`text-xs font-semibold mt-0.5 ${( value ?? 0 ) > ( data[ key ] ?? 0 ) ? "text-emerald-600" : ( value ?? 0 ) < ( data[ key ] ?? 0 ) ? "text-rose-600" : "text-muted-foreground"}`}>
                                        {( value ?? 0 ) > ( data[ key ] ?? 0 ) ? "+" : ""}{( ( value ?? 0 ) - ( data[ key ] ?? 0 ) ).toFixed(1)} vs
                                        baseline
                                    </p>
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>

            {/* Explanation */}
            <Card className="border-border bg-muted">
                <CardContent className="pt-4 text-sm text-foreground space-y-1">
                    <p className="font-semibold flex items-center gap-2"><Info className="w-4 h-4"/> Summary</p>
                    <p>{explanation}</p>
                    {components && (
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-3 text-xs">
                            <div><span className="text-muted-foreground">Reserve Adequacy Ratio:</span>
                                <strong>{components.reserve_adequacy_ratio ?? "—"}×</strong></div>
                            <div><span className="text-muted-foreground">Funding Ratio:</span>
                                <strong>{components.funding_ratio ?? "—"}×</strong></div>
                            <div><span className="text-muted-foreground">Levy Volatility (CV):</span>
                                <strong>{components.volatility != null ? `${( components.volatility * 100 ).toFixed(2)}%` : "—"}</strong>
                            </div>
                            <div><span className="text-muted-foreground">Shock Probability:</span>
                                <strong>{components.shock_probability ?? "—"}%</strong></div>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Radar + Group Breakdown */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2"><BarChart3
                            className="w-5 h-5 text-primary"/> Component Radar</CardTitle>
                        <CardDescription>Four dimensions weighted by importance.
                            Baseline{isWhatIf ? " vs Scenario" : ""}.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="h-72">
                            <ResponsiveContainer width="100%" height="100%">
                                <RadarChart data={radarData}>
                                    <PolarGrid/>
                                    <PolarAngleAxis dataKey="subject" tick={{fontSize: 10}}/>
                                    <Radar name="Baseline" dataKey="baseline" stroke="#2F4F4F" fill="#2F4F4F"
                                           fillOpacity={0.25}/>
                                    {isWhatIf && (
                                        <Radar name="What-If" dataKey="whatif" stroke="#E07A5F" fill="#E07A5F"
                                               fillOpacity={0.25}/>
                                    )}
                                </RadarChart>
                            </ResponsiveContainer>
                        </div>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2"><Building2 className="w-5 h-5 text-primary"/> By
                            Ownership Group</CardTitle>
                        <CardDescription>LSS breakdown by Apartment and Townhouse groups.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        {groupScores.length > 0 ? (
                            <>
                                <div className="h-40 mb-4">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <BarChart data={groupBarData} margin={{top: 4, right: 16, bottom: 4, left: 8}}>
                                            <CartesianGrid strokeDasharray="3 3" vertical={false}/>
                                            <XAxis dataKey="name" tick={{fontSize: 12}}/>
                                            <YAxis domain={[0, 100]} tick={{fontSize: 10}}/>
                                            <RTooltip/>
                                            <Bar dataKey="score" radius={[4, 4, 0, 0]}>
                                                {groupBarData.map((entry, i) => (
                                                    <Cell key={i}
                                                          fill={entry.score >= 70 ? "#2F4F4F" : entry.score >= 55 ? "#F2CC8F" : "#E07A5F"}/>
                                                ))}
                                            </Bar>
                                        </BarChart>
                                    </ResponsiveContainer>
                                </div>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                    {groupScores.map((g) => (
                                        <GroupScoreCard key={g.group} group={g}/>
                                    ))}
                                </div>
                            </>
                        ) : (
                            <p className="text-muted-foreground text-sm text-center py-8">Group scores not available.
                                Recompute to generate.</p>
                        )}
                    </CardContent>
                </Card>
            </div>

            {/* Detailed Component Cards */}
            <div>
                <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
                    <Activity className="w-5 h-5 text-primary"/> Score Component Detail
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
                    {["reserve_score", "shock_score", "volatility_score", "funding_score"].map((key) => (
                        <ComponentCard
                            key={key}
                            scoreKey={key}
                            value={activeData[ key ]}
                            isWhatIf={isWhatIf}
                            baseValue={data[ key ]}
                        />
                    ))}
                </div>
            </div>

            {/* What-If Modelling Panel */}
            {canManage && (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2"><SlidersHorizontal
                            className="w-5 h-5 text-primary"/> What-If Modelling</CardTitle>
                        <CardDescription>
                            Simulate how the Levy Stability Score changes if the EC takes action: increasing sinking
                            fund contributions,
                            deferring capital projects, or applying a loan to fund major works.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-8">
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">

                            {/* Sinking Fund Increase */}
                            <div className="space-y-4">
                                <div className="flex justify-between items-center">
                                    <Label className="font-semibold">Increase Sinking Fund Contribution</Label>
                                    <span className="font-semibold text-primary">{aud(sinkingIncrease)}/unit/qtr</span>
                                </div>
                                <Slider
                                    value={[sinkingIncrease]}
                                    onValueChange={([v]) => setSinkingIncrease(v)}
                                    min={0} max={1000} step={25}
                                />
                                <div
                                    className="rounded-lg bg-muted border p-3 text-xs text-muted-foreground space-y-1">
                                    <p><strong>Effect:</strong> Raises the sinking fund income
                                        by {aud(sinkingIncrease * 87)}/qtr (all 87 lots).</p>
                                    <p>Higher contributions increase the <strong>Funding
                                        Sufficiency</strong> and <strong>Reserve Adequacy</strong> scores.</p>
                                    {sinkingIncrease > 0 && (
                                        <p className="text-emerald-700 font-semibold">Annual
                                            increase: {aud(sinkingIncrease * 87 * 4)}</p>
                                    )}
                                </div>
                            </div>

                            {/* Capital Deferral */}
                            <div className="space-y-4">
                                <div className="flex justify-between items-center">
                                    <Label className="font-semibold">Defer Capital Projects (years)</Label>
                                    <span
                                        className="font-semibold text-primary">{deferYears} yr{deferYears !== 1 ? "s" : ""}</span>
                                </div>
                                <Slider
                                    value={[deferYears]}
                                    onValueChange={([v]) => setDeferYears(v)}
                                    min={0} max={10} step={1}
                                />
                                <div
                                    className="rounded-lg bg-muted border p-3 text-xs text-muted-foreground space-y-1">
                                    <p><strong>Effect:</strong> Pushes scheduled capital
                                        replacements {deferYears} year{deferYears !== 1 ? "s" : ""} into the future.</p>
                                    <p>Reduces near-term capital obligations, improving <strong>Reserve
                                        Adequacy</strong> but may increase long-term risk.</p>
                                    {deferYears > 5 &&
                                        <p className="text-amber-700 font-semibold">⚠ Deferral beyond 5 years may
                                            increase deterioration risk.</p>}
                                </div>
                            </div>

                            {/* Loan Financing */}
                            <div className="space-y-4">
                                <Label className="font-semibold">Loan Financing</Label>
                                <div className="space-y-3">
                                    <div>
                                        <Label className="text-xs text-muted-foreground">Loan Amount ($)</Label>
                                        <Input
                                            type="number" min={0} step={10000}
                                            value={loanAmount || ""}
                                            onChange={(e) => setLoanAmount(parseFloat(e.target.value) || 0)}
                                            placeholder="e.g. 500000"
                                            className="mt-1"
                                        />
                                    </div>
                                    <div className="grid grid-cols-2 gap-2">
                                        <div>
                                            <Label className="text-xs text-muted-foreground">Interest Rate (%)</Label>
                                            <Input
                                                type="number" min={0} max={20} step={0.1}
                                                value={loanRate}
                                                onChange={(e) => setLoanRate(parseFloat(e.target.value) || 5)}
                                                className="mt-1"
                                            />
                                        </div>
                                        <div>
                                            <Label className="text-xs text-muted-foreground">Term (years)</Label>
                                            <Input
                                                type="number" min={1} max={25} step={1}
                                                value={loanTerm}
                                                onChange={(e) => setLoanTerm(parseInt(e.target.value) || 10)}
                                                className="mt-1"
                                            />
                                        </div>
                                    </div>
                                </div>
                                {loanAmount > 0 && (
                                    <div
                                        className="rounded-lg bg-muted border p-3 text-xs text-muted-foreground space-y-1">
                                        <p><strong>Effect:</strong> Adds {aud(loanAmount)} to the reserve immediately
                                            (improves Reserve Adequacy).</p>
                                        <p>Annual repayment
                                            ≈ {aud(loanRate > 0 ? loanAmount * ( loanRate / 100 * Math.pow(1 + loanRate / 100, loanTerm) ) / ( Math.pow(1 + loanRate / 100, loanTerm) - 1 ) : loanAmount / loanTerm)} reduces
                                            net projected contributions.</p>
                                    </div>
                                )}
                            </div>
                        </div>

                        <div className="flex flex-col sm:flex-row items-center gap-4">
                            <Button
                                size="lg" onClick={handleWhatIf} disabled={whatIfLoading}
                                className="gap-2 w-full sm:w-auto"
                            >
                                <Target className={`w-4 h-4 ${whatIfLoading ? "animate-spin" : ""}`}/>
                                {whatIfLoading ? "Simulating…" : "Run What-If Scenario"}
                            </Button>
                            {isWhatIf && (
                                <div
                                    className={`flex items-center gap-3 rounded-xl border px-4 py-2 font-semibold ${lss > baseLss ? "bg-emerald-50 border-emerald-200 text-emerald-700" : lss < baseLss ? "bg-rose-50 border-rose-200 text-rose-700" : "bg-muted border-border text-muted-foreground"}`}>
                                    <TrendingUp className="w-5 h-5"/>
                                    Scenario LSS: <span className="text-xl font-semibold">{lss}</span>
                                    <span
                                        className="text-sm">({lss > baseLss ? "+" : ""}{( lss - baseLss ).toFixed(1)} vs baseline {baseLss})</span>
                                </div>
                            )}
                        </div>

                        {/* What-if component comparison */}
                        {isWhatIf && (
                            <motion.div initial={{opacity: 0, y: 8}} animate={{opacity: 1, y: 0}} className="space-y-3">
                                <Separator/>
                                <p className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Component
                                    Score Changes</p>
                                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                                    {["reserve_score", "shock_score", "volatility_score", "funding_score"].map(key => {
                                        const base = data[ key ] ?? 0;
                                        const sim = whatIfResult[ key ] ?? 0;
                                        const delta = sim - base;
                                        const info = componentInfo(key);
                                        const Icon = info.icon;
                                        return (
                                            <div key={key}
                                                 className={`rounded-xl border p-3 ${delta > 0 ? "bg-emerald-50 border-emerald-200" : delta < 0 ? "bg-rose-50 border-rose-200" : "bg-muted"}`}>
                                                <div className="flex items-center gap-1.5 mb-2">
                                                    <Icon className="w-4 h-4 text-muted-foreground"/>
                                                    <span className="text-xs font-semibold">{info.label}</span>
                                                </div>
                                                <div className="flex items-end gap-2">
                                                    <span className="text-muted-foreground text-xs">{base}</span>
                                                    <span className="text-muted-foreground text-xs">→</span>
                                                    <span className="text-xl font-semibold">{sim}</span>
                                                </div>
                                                <div
                                                    className={`text-xs font-bold mt-1 ${delta > 0 ? "text-emerald-600" : delta < 0 ? "text-rose-600" : "text-muted-foreground"}`}>
                                                    {delta > 0 ? "+" : ""}{delta.toFixed(1)} pts
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            </motion.div>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Methodology */}
            <Card className="border-primary/20 bg-primary/10">
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm flex items-center gap-2 text-primary">
                        <Info className="w-4 h-4"/> LSS Methodology
                    </CardTitle>
                </CardHeader>
                <CardContent className="text-xs text-primary space-y-2">
                    <p>
                        <strong>LSS = 0.35 × Reserve Adequacy + 0.30 × Capital Shock + 0.20 × Levy Volatility + 0.15 ×
                            Funding Sufficiency</strong>
                    </p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
                        <p><strong>Reserve Adequacy (35%):</strong> Reserve balance ÷ total capital required. ≥1.5× →
                            100pts.</p>
                        <p><strong>Capital Shock (30%):</strong> Probability of a special levy
                            within {horizon} years. &lt;5% → 100pts.</p>
                        <p><strong>Levy Volatility (20%):</strong> Coefficient of variation across per-unit
                            levies. &lt;5% → 100pts.</p>
                        <p><strong>Funding Sufficiency (15%):</strong> Projected contributions ÷ capital required. ≥1.2×
                            → 100pts.</p>
                    </div>
                    <p className="text-primary mt-1">
                        Scores are computed from the sinking fund plan, building assets, capital shock analysis, and
                        levy ledger data.
                        Recompute after updating the sinking fund plan or capital works schedule for fresh results.
                    </p>
                </CardContent>
            </Card>
        </div>
    );
}
