// @featuretrace:levy-fairness — Levy Fairness: subsidy-map simulation showing how levy burden
//   shifts across unit-entitlement groups under different allocation models.
// Layer: frontend
// Data flow: this page → GET /intelligence/levy-fairness(+/facilities,/groups), POST
//            /intelligence/levy-fairness/recompute → services/levy_fairness_service.py
//            (building-scoped).
// Related: backend/routers/intelligence.py
//           backend/services/levy_fairness_service.py
"use client";

import React, { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { toast } from "sonner";
import { seriesColor } from "@/lib/chartTheme";
import {
    AlertTriangle,
    ArrowLeft,
    ArrowRight,
    BarChart3,
    Building,
    Building2,
    Check,
    CheckCircle,
    ChevronDown,
    ChevronUp,
    Download,
    Edit2,
    Eye,
    FileText,
    Gavel,
    HelpCircle,
    Info,
    Layers,
    Minus,
    Pencil,
    PieChart as PieIcon,
    Plus,
    RefreshCw,
    Save,
    Scale,
    Search,
    SlidersHorizontal,
    Trash2,
    TrendingDown,
    TrendingUp,
    Users,
    X,
    XCircle
} from "lucide-react";

import { useAuth } from "../../contexts/AuthContext";
import {useActiveUnit} from '../../hooks/useActiveUnit';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Separator } from "../../components/ui/separator";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from "../../components/ui/select";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "../../components/ui/dialog";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger, } from "../../components/ui/tooltip";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { Slider } from "../../components/ui/slider";
import {PageHeader} from "../../components/shared/PageHeader";
import {
    Area,
    AreaChart,
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    Legend,
    ResponsiveContainer,
    Tooltip as RTooltip,
    XAxis,
    YAxis
} from "recharts";

const UnitExplainDrawer = lazy(() => import('../../components/levy/UnitExplainDrawer').then(m => ( {default: m.UnitExplainDrawer} )));
const ModelConfidenceBadge = lazy(() => import('../../components/levy/ModelConfidenceBadge').then(m => ( {default: m.ModelConfidenceBadge} )));
const LevyDistributionHistogram = lazy(() => import('../../components/levy/LevyDistributionHistogram').then(m => ( {default: m.LevyDistributionHistogram} )));
const ScenarioSnapshotPanel = lazy(() => import('../../components/levy/ScenarioSnapshotPanel').then(m => ( {default: m.ScenarioSnapshotPanel} )));
const FairnessAuditLog = lazy(() => import('../../components/levy/FairnessAuditLog').then(m => ( {default: m.FairnessAuditLog} )));
const CrossSubsidyTable = lazy(() => import('../../components/levy/CrossSubsidyTable').then(m => ( {default: m.CrossSubsidyTable} )));

// ─── Constants ────────────────────────────────────────────────────────────────

const MANAGER_ROLES = ["super_admin", "strata_manager", "ec_member"];

const DRIVER_LABELS = {
    unit_entitlement: "Unit Entitlement",
    equal_split: "Equal Split",
    car_space_weighted: "Car Spaces",
    area_weighted: "Internal Area",
    metered_usage: "Metered Usage",
    custom_weights: "Custom Weights"
};

const TIERS = {
    global: {
        label: "Global (All Owners)",
        short: "Global",
        color: "bg-primary/10 border-primary/20 text-primary",
        badge: "bg-primary/10 text-primary",
        dot: "bg-primary"
    },
    apartment: {
        label: "Apartment-Only",
        short: "Apartment",
        color: "bg-amber-50 border-amber-200 text-amber-800",
        badge: "bg-amber-100 text-amber-800",
        dot: "bg-amber-500"
    },
    townhouse: {
        label: "Townhouse-Only",
        short: "Townhouse",
        color: "bg-primary/10 border-primary/20 text-primary",
        badge: "bg-primary/10 text-primary",
        dot: "bg-primary"
    },
};
// Plain-language for each `missing_inputs` key the service can return. A key with no
// entry falls back to the key itself rather than being hidden, so a new backend reason
// is visible the day it ships instead of silently disappearing from this banner.
const MISSING_INPUT_LABELS = {
    levy_rates: "No levy rates are recorded for the current year, so there is nothing to redistribute.",
    facilities: "No facilities are registered, so no benefit can be attributed to any group.",
    facility_cost_basis: "Facilities are registered but none carry a cost, so shares cannot be weighted.",
    zero_sum_violation: "The benefit total and the levy total disagree — the two sides are measuring different periods or scopes.",
    capital_items_unresolved: "One or more capital works items name an asset that is not in the register, so their cost is being spread across lots the work may not serve.",
};

// Levy history is loaded from the API (data.levy_history) — no hardcoded constants

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: aud
 * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
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
 * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const pct = (n, decimals = 1) =>
    n == null ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(decimals)}%`;
/**
 * @generated FunctionHeader
 * Function: lbfiBand
 * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function lbfiBand(score) {
    if (score >= 98) return {
        label: "Excellent",
        color: "text-emerald-600",
        bg: "bg-emerald-50 border-emerald-200",
        ring: "border-emerald-400",
        icon: CheckCircle
    };
    if (score >= 94) return {
        label: "Good",
        color: "text-primary",
        bg: "bg-primary/10 border-primary/20",
        ring: "border-primary/20",
        icon: CheckCircle
    };
    if (score >= 88) return {
        label: "Moderate",
        color: "text-amber-600",
        bg: "bg-amber-50 border-amber-200",
        ring: "border-amber-400",
        icon: AlertTriangle
    };
    return {
        label: "Poor",
        color: "text-rose-600",
        bg: "bg-rose-50 border-rose-200",
        ring: "border-rose-500",
        icon: XCircle
    };
}
// ─── Sub-components: Overview ─────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: LbfiHero
 * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function LbfiHero({lbfi, onRecompute, canRecompute, recomputing, computedAt}) {
    const score = lbfi?.current_score ?? 0;
    const gain = lbfi?.fairness_gain ?? 0;
    const band = lbfiBand(score);
    const BandIcon = band.icon;

    return (
        <div className={`rounded-2xl border-2 p-6 ${band.bg} flex flex-col md:flex-row items-center gap-6`}>
            {/* Score ring */}
            <div
                className={`relative flex-shrink-0 w-36 h-36 rounded-full border-8 ${band.ring} flex items-center justify-center shadow-sm`}>
                <div className="text-center">
                    <div className={`text-5xl font-semibold ${band.color}`}>{score}</div>
                    <div className="text-xs text-muted-foreground font-medium mt-0.5">/ 100</div>
                </div>
            </div>

            {/* Text block */}
            <div className="flex-1 space-y-2">
                <div className="flex items-center gap-2 flex-wrap">
                    <h2 className="text-xl font-bold">Levy Benefit Fairness Index (LBFI)</h2>
                    <Badge variant="outline" className={`${band.color} font-semibold`}>
                        <BandIcon className="w-3 h-3 mr-1"/>{band.label}
                    </Badge>
                    <TooltipProvider>
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <HelpCircle className="w-4 h-4 text-muted-foreground cursor-help"/>
                            </TooltipTrigger>
                            <TooltipContent className="max-w-xs text-xs">
                                LBFI = 100 × (1 − D) where D = ½ × Σ|p_i − b_i| (Gini-style gap).
                                p_i = lot payment share (UE-based), b_i = lot benefit share (by facility).
                                100 = perfect alignment. &lt;88 = significant cross-subsidies.
                            </TooltipContent>
                        </Tooltip>
                    </TooltipProvider>
                </div>

                <p className={`text-sm font-medium ${band.color}`}>{lbfi?.interpretation}</p>

                <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                    <span>Gini-gap D = <strong>{lbfi?.D ?? "—"}</strong></span>
                    <span className="flex items-center gap-1">
            Benefit-based model: <strong className="text-emerald-600 ml-1">100 / 100</strong>
            <span className="ml-1 text-emerald-600 font-semibold">(+{gain} pts gain)</span>
          </span>
                    {computedAt && <span>Last computed: {new Date(computedAt).toLocaleString("en-AU")}</span>}
                </div>
            </div>

            {canRecompute && (
                <Button variant="outline" size="sm" onClick={onRecompute} disabled={recomputing}
                        className="flex-shrink-0">
                    <RefreshCw className={`w-4 h-4 mr-2 ${recomputing ? "animate-spin" : ""}`}/>
                    {recomputing ? "Computing…" : "Recompute"}
                </Button>
            )}
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: ExplainerBox
 * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ExplainerBox({buildingName}) {
    return (
        <Card className="border-primary/20 bg-primary/10">
            <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-2 text-primary">
                    <Info className="w-4 h-4"/> What is this analysis?
                </CardTitle>
            </CardHeader>
            <CardContent className="text-xs text-primary space-y-2">
                <p>
                    Under the current flat <strong>Unit of Entitlement (UOE)</strong> model, all owners pay
                    proportionally to their Unit of Entitlement.
                    However, many complexes like <strong>{buildingName || 'this one'}</strong> have mixed-use or
                    multi-tiered facilities.
                </p>
                <p>
                    Different unit types often use different facilities (e.g. <strong>lifts, corridor cleaning, or
                    specific parking systems</strong>)
                    that other lots may never access. This creates a <strong>cross-subsidy</strong> where some owners
                    partially fund facilities
                    they will never use.
                </p>
                <p>
                    The <strong>LBFI</strong> (Levy Benefit Fairness Index) quantifies this gap. A score of 100 means
                    every lot pays exactly in
                    proportion to the facilities it uses. The <strong>Benefit-Based model</strong> shows what each
                    lot <em>would</em> pay
                    if costs were allocated purely by usage — while collecting the exact same total building levy.
                </p>
            </CardContent>
        </Card>
    );
}
/**
 * @generated FunctionHeader
 * Function: SubsidyFlowCard
 * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function SubsidyFlowCard({subsidyMap}) {
    if (!subsidyMap) return null;
    const {flows = [], group_summary = [], total_cost_basis = 0} = subsidyMap;

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Scale className="w-5 h-5 text-primary"/> Subsidy Map
                    <TooltipProvider>
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Info className="w-4 h-4 text-muted-foreground cursor-help ml-1"/>
                            </TooltipTrigger>
                            <TooltipContent className="max-w-xs text-xs">
                                S_i = (p_i − b_i) × T. Positive = overpays (subsidises others).
                                Flows allocated proportionally: Flow(g→h) = S_g × (−S_h) / Σrecipients.
                            </TooltipContent>
                        </Tooltip>
                    </TooltipProvider>
                </CardTitle>
                <CardDescription>
                    Who subsidises whom under the current UE-only scheme. Total cost basis:{" "}
                    <strong>{aud(total_cost_basis)}</strong>/yr
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
                {/* Group net positions */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {group_summary.map((g) => (
                        <div key={g.group}
                             className={`rounded-xl border p-4 text-center ${
                                 g.role === "Contributor" ? "bg-rose-50 border-rose-200" :
                                     g.role === "Recipient" ? "bg-emerald-50 border-emerald-200" :
                                         "bg-muted border-border"
                             }`}>
                            <div className="flex items-center justify-center gap-1.5 mb-1">
                                <Building2 className="w-4 h-4 text-muted-foreground"/>
                                <span className="font-semibold text-sm">{g.group}s ({g.count} lots, UOE {g.ue})</span>
                            </div>
                            <div
                                className={`text-2xl font-semibold ${g.net_subsidy > 0 ? "text-rose-600" : g.net_subsidy < 0 ? "text-emerald-600" : "text-muted-foreground"}`}>
                                {g.net_subsidy >= 0 ? "+" : ""}{aud(Math.abs(g.net_subsidy))}
                            </div>
                            <Badge variant="outline" className={`mt-2 text-xs ${
                                g.role === "Contributor" ? "text-rose-600 border-rose-300" :
                                    g.role === "Recipient" ? "text-emerald-600 border-emerald-300" :
                                        "text-muted-foreground"}`}>
                                {g.role === "Contributor" ? "Overpays (subsidises others)" :
                                    g.role === "Recipient" ? "Underpays (subsidised)" : "Neutral"}
                            </Badge>
                            <div className="text-xs text-muted-foreground mt-1">
                                Current: {aud(g.current_total)} →
                                Benefit-based: {aud(g.benefit_total)} ({pct(g.change_pct)})
                            </div>
                        </div>
                    ))}
                </div>

                {/* Flow arrows */}
                {flows.length > 0 && (
                    <div className="space-y-2">
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Annual
                            Subsidy Flows</p>
                        {flows.map((flow, i) => (
                            <motion.div key={i} initial={{opacity: 0, x: -8}} animate={{opacity: 1, x: 0}}
                                        transition={{delay: i * 0.06}}
                                        className="flex items-center gap-3 rounded-lg bg-muted border border-border px-4 py-3">
                                <span className="font-semibold text-rose-700 text-sm w-28">{flow.from}s</span>
                                <ArrowRight className="w-4 h-4 text-muted-foreground flex-shrink-0"/>
                                <span className="font-semibold text-emerald-700 text-sm w-28">{flow.to}s</span>
                                <span className="ml-auto font-semibold text-foreground text-sm">
                  {aud(flow.amount)}<span className="text-xs font-normal text-muted-foreground">/yr</span>
                </span>
                            </motion.div>
                        ))}
                        <p className="text-xs text-muted-foreground italic">
                            {flows[ 0 ].from} owners currently contribute {aud(flows[ 0 ]?.amount)} per year toward
                            facilities modelled as serving {flows[ 0 ].to} only. Under a benefit-based model this
                            amount would be redirected back to {flows[ 0 ].from} owners.
                        </p>
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
/**
 * @generated FunctionHeader
 * Function: ScenarioComparisonCard
 * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ScenarioComparisonCard({lbfi, impact}) {
    const current = lbfi?.current_score ?? 0;
    const benefit = lbfi?.benefit_score ?? 100;
    const gain = lbfi?.fairness_gain ?? 0;
    const cb = lbfiBand(current);
    const bb = lbfiBand(benefit);

    const chartData = ( impact || [] ).map((r) => ( {
        name: `${r.group}s`,
        Current: Math.round(r.current_total),
        "Benefit-Based": Math.round(r.benefit_total),
    } ));

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2"><BarChart3 className="w-5 h-5 text-primary"/>Scenario
                    Comparison</CardTitle>
                <CardDescription>Current UE-based model vs. benefit-based redistribution (same total levy
                    collected).</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                {/* Score comparison */}
                <div className="grid grid-cols-3 gap-4 items-center">
                    <div className={`rounded-xl border p-4 text-center ${cb.bg}`}>
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">Current
                            (UE Only)</p>
                        <div className={`text-4xl font-semibold ${cb.color}`}>{current}</div>
                        <Badge variant="outline" className={`mt-2 ${cb.color}`}>{cb.label}</Badge>
                    </div>
                    <div className="flex flex-col items-center gap-1">
                        <ArrowRight className="w-8 h-8 text-muted-foreground"/>
                        <div className="text-center">
                            <div className="text-xl font-semibold text-emerald-600">+{gain}</div>
                            <p className="text-xs text-muted-foreground">Fairness Gain</p>
                        </div>
                    </div>
                    <div className={`rounded-xl border p-4 text-center ${bb.bg}`}>
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1">Benefit-Based</p>
                        <div className={`text-4xl font-semibold ${bb.color}`}>{benefit}</div>
                        <Badge variant="outline" className={`mt-2 ${bb.color}`}>{bb.label}</Badge>
                    </div>
                </div>

                {/* Bar chart */}
                {chartData.length > 0 && (
                    <div className="h-52">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={chartData} margin={{top: 4, right: 16, left: 8, bottom: 4}}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false}/>
                                <XAxis dataKey="name" tick={{fontSize: 12}}/>
                                <YAxis tickFormatter={(v) => `$${( v / 1000 ).toFixed(0)}k`} tick={{fontSize: 10}}/>
                                <RTooltip formatter={(v) => aud(v)}/>
                                <Legend iconSize={10} wrapperStyle={{fontSize: 11}}/>
                                <Bar dataKey="Current" fill="#2F4F4F" radius={[3, 3, 0, 0]}/>
                                <Bar dataKey="Benefit-Based" fill="#E07A5F" radius={[3, 3, 0, 0]}/>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                )}
                <p className="text-xs text-muted-foreground text-center">
                    Under the benefit-based model each lot pays exactly in proportion to the facilities it uses — total
                    levy collected stays the same at {aud(lbfi?.current_score != null ? undefined : undefined)}.
                </p>
            </CardContent>
        </Card>
    );
}
/**
 * @generated FunctionHeader
 * Function: LegalPathwayCard
 * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function LegalPathwayCard() {
    const paths = [
        {
            num: "1",
            title: "Differential Contribution + Cost-Allocation By-Law",
            badge: "Recommended",
            badgeClass: "bg-emerald-100 text-emerald-800",
            icon: Gavel,
            vote: "Special Resolution (75% of vote value)",
            complexity: "Low–Medium",
            // Two distinct, verified legal levers, not one: (1) a differential
            // financial contribution — ACT Unit Titles (Management) Act 2011 s.78
            // (Admin Fund) / s.89 (Sinking Fund), or NSW SSMA 2015 s.82/83 —
            // requires the OC to show the paying lots impose disproportionate
            // running/maintenance costs; and (2) for NSW schemes, a by-law under
            // SSMA 2015 s.107 (Common Property Memorandum) assigning maintenance
            // responsibility for specific common property to those lots. An
            // earlier version of this card cited s.78/s.107 together under the
            // single label "Exclusive Use By-Law" — s.78 authorises the financial
            // contribution, it does not itself create a by-law, and s.107 is the
            // Common Property Memorandum mechanism, not a distinct "exclusive use"
            // provision. The ACT by-law equivalent to s.107 was not confirmed
            // against current legislation — verify with a strata lawyer before
            // drafting a resolution.
            desc: "Two complementary levers via Special Resolution: a differential financial contribution (ACT UTMA 2011 s.78/s.89, or NSW SSMA 2015 s.82/83) once the OC can show Apartment lots impose disproportionate running/maintenance costs, paired — for NSW schemes — with a Common Property Memorandum by-law (SSMA 2015 s.107) assigning maintenance responsibility for apartment-specific common property (lifts, fire pumps, corridor cleaning). Keeps the single strata plan intact. Confirm the exact ACT by-law mechanism with a strata lawyer before drafting.",
        },
        {
            num: "2",
            title: "Stratum Subdivision + BMC",
            badge: "Structural",
            badgeClass: "bg-muted text-foreground",
            icon: Layers,
            vote: "Tribunal / Court approval + subdivision process",
            complexity: "Very High",
            desc: "Formally separates the scheme into two strata plans (Apartments & Townhouses) reporting to a Building Management Committee. Each scheme manages its own budget. Gold-standard for fairness in large mixed-use schemes — but major process.",
        },
        {
            num: "3",
            title: "UOE Re-apportionment",
            badge: "Last Resort",
            badgeClass: "bg-rose-100 text-rose-800",
            icon: Scale,
            vote: "ACAT (ACT) / NCAT (NSW) order required",
            complexity: "High — contentious",
            desc: "Challenge the registered Unit of Entitlement via tribunal. Involves a registered valuer. If Townhouse UOE decreases relative to Apartments, their share of all levies automatically reduces — but changes voting power too.",
        },
    ];

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2"><Gavel className="w-5 h-5 text-primary"/>Legal Pathways
                    to a Fairer Levy</CardTitle>
                <CardDescription>Three recognised approaches under Australian strata law. Option 1 is the most practical
                    for an established complex.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                {paths.map((p) => {
                    const Icon = p.icon;
                    return (
                        <div key={p.num} className="rounded-xl border p-4 hover:shadow-sm transition-shadow">
                            <div className="flex items-start gap-3">
                                <div
                                    className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
                                    <span className="text-sm font-semibold text-primary">{p.num}</span>
                                </div>
                                <div className="flex-1 space-y-2">
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <h4 className="font-semibold text-sm">{p.title}</h4>
                                        <span
                                            className={`text-xs font-semibold px-2 py-0.5 rounded-full ${p.badgeClass}`}>{p.badge}</span>
                                    </div>
                                    <p className="text-xs text-muted-foreground">{p.desc}</p>
                                    <div className="flex items-center gap-4 text-xs">
                                        <span className="text-muted-foreground">Vote: <strong>{p.vote}</strong></span>
                                        <span
                                            className="text-muted-foreground">Complexity: <strong>{p.complexity}</strong></span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    );
                })}
            </CardContent>
        </Card>
    );
}
// ─── Sub-components: Impact Tab ───────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: UnitImpactSummary
 * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function UnitImpactSummary({unitImpact}) {
    const total = unitImpact.length;
    const saving = unitImpact.filter((u) => u.change < -0.5);
    const rising = unitImpact.filter((u) => u.change > 0.5);
    const neutral = unitImpact.filter((u) => Math.abs(u.change) <= 0.5);
    const avgSave = saving.length ? saving.reduce((s, u) => s + u.change, 0) / saving.length : 0;
    const avgRise = rising.length ? rising.reduce((s, u) => s + u.change, 0) / rising.length : 0;
    const totalCurrent = unitImpact.reduce((s, u) => s + u.current_levy, 0);
    const totalProposed = unitImpact.reduce((s, u) => s + u.proposed_levy, 0);

    const stats = [
        {
            label: "Total Lots",
            value: total,
            sub: "in this analysis",
            color: "text-foreground",
            bg: "bg-muted border-border"
        },
        {
            label: "Levy Decrease",
            value: saving.length,
            sub: `avg ${aud(avgSave)}/yr`,
            color: "text-emerald-700",
            bg: "bg-emerald-50 border-emerald-200"
        },
        {
            label: "Levy Increase",
            value: rising.length,
            sub: `avg +${aud(avgRise)}/yr`,
            color: "text-rose-700",
            bg: "bg-rose-50 border-rose-200"
        },
        {
            label: "Effectively Neutral",
            value: neutral.length,
            sub: "< $0.50 change",
            color: "text-muted-foreground",
            bg: "bg-muted border-border"
        },
    ];

    return (
        <div className="space-y-3">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                {stats.map((s) => (
                    <div key={s.label} className={`rounded-xl border p-4 ${s.bg}`}>
                        <div className={`text-3xl font-semibold ${s.color}`}>{s.value}</div>
                        <div className="font-semibold text-sm mt-1">{s.label}</div>
                        <div className="text-xs text-muted-foreground mt-0.5">{s.sub}</div>
                    </div>
                ))}
            </div>
            <div className="rounded-lg border bg-primary/10 border-primary/20 px-4 py-3 text-xs text-primary">
                <strong>Revenue neutral:</strong> Total scheme levy unchanged at <strong>{aud(totalCurrent)}</strong>.
                This shows how the same levy would be redistributed if each owner paid for the facilities their unit
                type uses.
                Total proposed: <strong>{aud(totalProposed)}</strong> (difference
                of {aud(Math.abs(totalProposed - totalCurrent))} due to rounding).
            </div>
        </div>
    );
}
// ─── Sub-components: Facilities Tab ──────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: TierSummaryRow
 * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function TierSummaryRow({facilities, groupsById}) {
    // Map benefit_group_id to tier key
    /**
     * @generated FunctionHeader
     * Function: getTier
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    // Tiers come from the scheme's OWN benefit groups, not from matching the words
    // "APARTMENT" and "TOWNHOUSE" in a group name. That match was East Gate's naming
    // written into the page, and it fails silently rather than loudly: once groups are
    // named "Group A"/"Group B" -- which is now the default, precisely so the naming
    // does not assume the answer -- every facility fell through to "global" and the
    // summary reported one undifferentiated bucket while the data underneath was split.
    const getTier = (fac) => {
        const gid = fac.benefit_group_id;
        if (gid && groupsById[ gid ]) return gid;
        return fac.tier || "global";
    };

    // Colour comes from seriesColor(), the canonical categorical palette, NOT from
    // Tailwind palette classes. Two reasons. A benefit group is a category, and the
    // semantic status colours (amber = warning, rose = breach) say something untrue
    // about it. And the number of groups is now unbounded -- a scheme may configure
    // five -- so a fixed list of hand-picked classes either runs out or cycles, and a
    // cycled palette silently gives two different groups the same colour. seriesColor
    // folds past its eighth entry into a neutral instead.
    const tierKeys = [ ...new Set(facilities.map(getTier)) ];
    const summary = tierKeys.map((key, i) => {
        const facs = facilities.filter((f) => getTier(f) === key);
        const total = facs.reduce((s, f) => s + ( f.annual_cost || 0 ), 0);
        const label = TIERS[ key ]?.label || groupsById[ key ] || key;
        return {key, label, accent: seriesColor(i), count: facs.length, total};
    });

    if (summary.length === 0) return null;

    return (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {summary.map((s) => (
                <div key={s.key} className="rounded-xl border border-border bg-card p-4"
                     style={{borderLeftWidth: 3, borderLeftColor: s.accent}}>
                    <div className="flex items-center gap-2 mb-2">
                        <span className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                              style={{backgroundColor: s.accent}}/>
                        <span className="font-semibold text-sm text-foreground">{s.label}</span>
                    </div>
                    <div className="text-2xl font-semibold">{aud(s.total)}</div>
                    <div className="text-xs mt-1 opacity-70">{s.count} {s.count === 1 ? "facility" : "facilities"}/yr
                    </div>
                </div>
            ))}
        </div>
    );
}
// ─── Main Page ────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: LevyFairnessPage
 * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const LevyFairnessPage = () => {
    const router = useRouter();
    const {user, api, hasFeatureAccess, selectedBuilding} = useAuth();
    const {activeUnit} = useActiveUnit();
    const _role = user?.effective_role || user?.role || "";
    const canManage = MANAGER_ROLES.includes(_role);
    const canEdit = MANAGER_ROLES.includes(_role);  // create/edit: all 4 management roles
    const canDelete = ["super_admin", "strata_manager"].includes(_role);  // delete: super_admin & strata_manager only

    // ── State ──────────────────────────────────────────────────────────────────
    const [data, setData] = useState(null);
    const [facilities, setFacilities] = useState([]);
    const [groups, setGroups] = useState([]);
    const [loading, setLoading] = useState(true);
    // Phase 1 containment state. `loadState` is a closed vocabulary — loading | ready |
    // incomplete | failed — so the page can never again render numbers for a result the
    // backend declined to compute. `missingInputs` names what is absent; `failedEndpoints`
    // names what did not answer.
    const [loadState, setLoadState] = useState("loading");
    const [missingInputs, setMissingInputs] = useState([]);
    const [failedEndpoints, setFailedEndpoints] = useState([]);

    // Class A/B split status — null means feature off or no split configured
    const [splitStatus, setSplitStatus] = useState(null);
    const [recomputing, setRecomputing] = useState(false);
    const [downloading, setDownloading] = useState(false);
    const [activeTab, setActiveTab] = useState("overview");

    // Group editing state
    const [editingGroupId, setEditingGroupId] = useState(null);
    const [groupDraft, setGroupDraft] = useState({});
    const [savingGroup, setSavingGroup] = useState(false);
    const [deletingGroupId, setDeletingGroupId] = useState(null);
    const [addGroupOpen, setAddGroupOpen] = useState(false);
    const [newGroupDraft, setNewGroupDraft] = useState({
        name: "", description: "", group_type: "custom",
        allocation_driver: "unit_entitlement",
        unit_prefixes: "", unit_number_range_min: "", unit_number_range_max: "",
        lot_numbers: ""
    });
    const [savingNewGroup, setSavingNewGroup] = useState(false);
    const [groupPreview, setGroupPreview] = useState({});  // {groupId: {matched_units, count}}

    // Simulation settings
    const [maxChangePct, setMaxChangePct] = useState(10);
    const [maxChangeAmt, setMaxChangeAmt] = useState(1000);

    // Unit explain drawer state
    const [explainUnit, setExplainUnit] = useState(null);
    const [explainOpen, setExplainOpen] = useState(false);

    // Search/filter state
    const [unitSearch, setUnitSearch] = useState("");
    const [unitTypeFilter, setUnitTypeFilter] = useState("all");
    const [unitSort, setUnitSort] = useState("change");
    const [unitSortDir, setUnitSortDir] = useState("asc");
    const [unitPage, setUnitPage] = useState(1);
    const [facSearch, setFacSearch] = useState("");

    // Facility inline editing state
    const [editingFacId, setEditingFacId] = useState(null);
    const [facDraft, setFacDraft] = useState({});
    const [savingFac, setSavingFac] = useState(false);
    const UNIT_PAGE_SIZE = 25;

    const groupsById = React.useMemo(() =>
            groups.reduce((acc, g) => ( {...acc, [ g.id ]: g.name} ), {}),
        [groups]
    );

    // ── Load ───────────────────────────────────────────────────────────────────
    const load = useCallback(async () => {
        setLoading(true);
        setLoadState("loading");
        setFailedEndpoints([]);
        try {
            // PHASE 1 CONTAINMENT.
            //
            // These four calls previously used `.catch(() => ({data: null}))` and
            // `.catch(() => ({data: []}))`. A failed request therefore became an empty
            // dataset indistinguishable from a real empty result, and the page rendered
            // a confident $0 for a figure nobody had computed. Each rejection is now
            // recorded with the endpoint that produced it, so a failure can be shown as
            // a failure and named.
            //
            // `Promise.allSettled` rather than `Promise.all`: one endpoint being down
            // must not blank the other three, but it must not be silent either.
            const endpoints = [
                "/intelligence/levy-fairness",
                "/intelligence/levy-fairness/facilities",
                "/intelligence/levy-fairness/groups",
                // Class A/B split status — legitimately absent when the feature is off,
                // which is why it is the one endpoint whose failure is not an error.
                "/scheme-classes/status",
            ];
            const settled = await Promise.allSettled(endpoints.map(url => api.get(url)));
            const failed = [];
            settled.forEach((r, i) => {
                // index 3 (/scheme-classes/status) is optional by design.
                if (r.status === "rejected" && i !== 3) failed.push(endpoints[i]);
            });
            const val = i => (settled[i].status === "fulfilled" ? settled[i].value.data : undefined);

            setFacilities(val(1) || []);
            setGroups(val(2) || []);
            setSplitStatus(val(3) || null);

            const fair = val(0);
            setData(fair ?? null);
            setFailedEndpoints(failed);

            if (failed.length) {
                setLoadState("failed");
            } else if (!fair) {
                setLoadState("failed");
            } else if (fair.status === "incomplete" || fair.insufficient_levy_data) {
                // The backend has said it could not compute this. Honour that instead of
                // rendering the numbers sitting beside the flag — which is the specific
                // defect this phase exists to remove.
                setLoadState("incomplete");
                setMissingInputs(fair.missing_inputs || []);
            } else {
                setLoadState("ready");
            }
        } catch (err) {
            console.error(err);
            setLoadState("failed");
            toast.error("Failed to load levy fairness data");
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        load();
    }, [load]);
    // ── Recompute ──────────────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handleRecompute
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRecompute = async () => {
        setRecomputing(true);
        try {
            const res = await api.post("/intelligence/levy-fairness/recompute", {
                max_change_percent: maxChangePct,
                max_change_amount: maxChangeAmt,
                run_monte_carlo: true
            });
            setData(res.data);
            toast.success("Analysis recomputed successfully");
        } catch {
            toast.error("Recompute failed");
        } finally {
            setRecomputing(false);
        }
    };
    // ── Group CRUD helpers ─────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: _parsePrefixes
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const _parsePrefixes = (str) => str.split(",").map(s => s.trim().toUpperCase()).filter(Boolean);
    /**
     * @generated FunctionHeader
     * Function: _parseLots
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const _parseLots = (str) => str.split(",").map(s => s.trim().toUpperCase()).filter(Boolean);
    /**
     * @generated FunctionHeader
     * Function: _buildGroupPayload
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const _buildGroupPayload = (draft) => ( {
        name: draft.name,
        description: draft.description || "",
        group_type: draft.group_type || "custom",
        allocation_driver: draft.allocation_driver || "unit_entitlement",
        unit_prefixes: _parsePrefixes(draft.unit_prefixes || ""),
        unit_number_range: ( draft.unit_number_range_min || draft.unit_number_range_max )
            ? {
                min: ( draft.unit_number_range_min || "" ).toUpperCase(),
                max: ( draft.unit_number_range_max || "" ).toUpperCase()
            }
            : null,
        lot_numbers: _parseLots(draft.lot_numbers || ""),
    } );
    /**
     * @generated FunctionHeader
     * Function: handleEditGroup
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleEditGroup = (bg) => {
        setEditingGroupId(bg.id);
        setGroupDraft({
            name: bg.name || "",
            description: bg.description || "",
            group_type: bg.group_type || "custom",
            allocation_driver: bg.allocation_driver || bg.allocation_rule?.allocation_type || "unit_entitlement",
            unit_prefixes: ( bg.unit_prefixes || [] ).join(", "),
            unit_number_range_min: bg.unit_number_range?.min || "",
            unit_number_range_max: bg.unit_number_range?.max || "",
            lot_numbers: ( bg.lot_numbers || [] ).join(", "),
        });
    };
    /**
     * @generated FunctionHeader
     * Function: handleSaveGroup
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSaveGroup = async (groupId) => {
        setSavingGroup(true);
        try {
            await api.put(`/intelligence/levy-fairness/groups/${groupId}`, _buildGroupPayload(groupDraft));
            toast.success("Benefit group saved");
            setEditingGroupId(null);
            const grpRes = await api.get("/intelligence/levy-fairness/groups");
            setGroups(grpRes.data || []);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "Save failed");
        } finally {
            setSavingGroup(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDeleteGroup
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDeleteGroup = async (groupId) => {
        setDeletingGroupId(groupId);
        try {
            await api.delete(`/intelligence/levy-fairness/groups/${groupId}`);
            toast.success("Benefit group deleted");
            const grpRes = await api.get("/intelligence/levy-fairness/groups");
            setGroups(grpRes.data || []);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "Delete failed");
        } finally {
            setDeletingGroupId(null);
        }
    };
    // ── Facility handlers ──────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handleEditFacility
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleEditFacility = (fac) => {
        setEditingFacId(fac.facility_id);
        setFacDraft({
            facility_name: fac.facility_name || "",
            annual_cost: fac.annual_cost ?? 0,
            allocation_driver: fac.allocation_driver || "unit_entitlement",
            benefit_group_id: fac.benefit_group_id || "",
        });
    };
    /**
     * @generated FunctionHeader
     * Function: handleSaveFacility
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSaveFacility = async (facilityId) => {
        setSavingFac(true);
        try {
            const payload = {
                ...facDraft,
                annual_cost: parseFloat(facDraft.annual_cost) || 0,
            };
            await api.put(`/intelligence/levy-fairness/facilities/${facilityId}`, payload);
            toast.success("Facility updated");
            setEditingFacId(null);
            const facRes = await api.get("/intelligence/levy-fairness/facilities");
            setFacilities(facRes.data || []);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "Save failed");
        } finally {
            setSavingFac(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleCreateGroup
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCreateGroup = async () => {
        if (!newGroupDraft.name.trim()) {
            toast.error("Name is required");
            return;
        }
        setSavingNewGroup(true);
        try {
            await api.post("/intelligence/levy-fairness/groups", _buildGroupPayload(newGroupDraft));
            toast.success("Benefit group created");
            setAddGroupOpen(false);
            setNewGroupDraft({
                name: "", description: "", group_type: "custom", allocation_driver: "unit_entitlement",
                unit_prefixes: "", unit_number_range_min: "", unit_number_range_max: "", lot_numbers: ""
            });
            const grpRes = await api.get("/intelligence/levy-fairness/groups");
            setGroups(grpRes.data || []);
        } catch (e) {
            toast.error(e?.response?.data?.detail || "Create failed");
        } finally {
            setSavingNewGroup(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handlePreviewGroup
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePreviewGroup = async (groupId) => {
        try {
            const res = await api.get(`/intelligence/levy-fairness/groups/${groupId}/preview`);
            setGroupPreview(prev => ( {...prev, [ groupId ]: res.data} ));
        } catch {
            toast.error("Preview failed");
        }
    };
    // ── Downloads ──────────────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handleDownloadFile
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownloadFile = async (endpoint, filename, type) => {
        setDownloading(true);
        try {
            const res = await api.get(endpoint, {responseType: "blob"});
            const url = URL.createObjectURL(new Blob([res.data], {type}));
            const link = document.createElement("a");
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
            toast.success(`${filename} downloaded`);
        } catch {
            toast.error("Download failed");
        } finally {
            setDownloading(false);
        }
    };

    const today = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    /**
     * @generated FunctionHeader
     * Function: handleDownloadPdf
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownloadPdf = () => handleDownloadFile("/intelligence/levy-fairness/agm-report.pdf", `EastGate_LevyEquity_${today}.pdf`, "application/pdf");
    /**
     * @generated FunctionHeader
     * Function: handleDownloadPptx
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownloadPptx = () => handleDownloadFile("/intelligence/levy-fairness/agm-presentation.pptx", `EastGate_LevyEquity_${today}.pptx`, "application/vnd.openxmlformats-officedocument.presentationml.presentation");
    /**
     * @generated FunctionHeader
     * Function: handleDownloadCsv
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownloadCsv = () => handleDownloadFile("/intelligence/levy-fairness/impact.csv", `EastGate_LevyImpact_${today}.csv`, "text/csv");

    // ── Unit Impact filtering/sorting ──────────────────────────────────────────
    const unitImpact = useMemo(() => data?.unit_impact || [], [ data ]);

    // The lot-type filter used to be two hardcoded options, "Apartments (70)" and
    // "Townhouses (17)". That is one scheme's composition written into the page: any
    // other building saw two filters that matched nothing, and East Gate saw stale
    // counts the moment a lot was added. Derived from the returned lots instead, so a
    // scheme with one lot type gets one option and a scheme with five gets five.
    const lotTypeOptions = useMemo(() => {
        const counts = new Map();
        for (const u of unitImpact) {
            const raw = ( u.unit_type || "" ).trim();
            if (!raw) continue;
            const key = raw.toLowerCase();
            const existing = counts.get(key);
            if (existing) existing.count += 1;
            else counts.set(key, {value: key, label: raw.charAt(0).toUpperCase() + raw.slice(1), count: 1});
        }
        return [ ...counts.values() ].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
    }, [ unitImpact ]);

    const filteredUnits = unitImpact
        .filter((u) => {
            const q = unitSearch.toLowerCase();
            const matchSearch = !q || u.unit_number?.toLowerCase().includes(q) || u.owner_name?.toLowerCase().includes(q);
            const matchType = unitTypeFilter === "all" || u.unit_type?.toLowerCase() === unitTypeFilter;
            return matchSearch && matchType;
        })
        .sort((a, b) => {
            let av = a[ unitSort ] ?? 0;
            let bv = b[ unitSort ] ?? 0;
            if (typeof av === "string") av = av.toLowerCase();
            if (typeof bv === "string") bv = bv.toLowerCase();
            return unitSortDir === "asc" ? ( av < bv ? -1 : av > bv ? 1 : 0 ) : ( av > bv ? -1 : av < bv ? 1 : 0 );
        });

    const totalPages = Math.ceil(filteredUnits.length / UNIT_PAGE_SIZE);
    const pagedUnits = filteredUnits.slice(( unitPage - 1 ) * UNIT_PAGE_SIZE, unitPage * UNIT_PAGE_SIZE);
    /**
     * @generated FunctionHeader
     * Function: toggleSort
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleSort = (col) => {
        if (unitSort === col) setUnitSortDir((d) => d === "asc" ? "desc" : "asc");
        else {
            setUnitSort(col);
            setUnitSortDir("asc");
        }
        setUnitPage(1);
    };
    /**
     * @generated FunctionHeader
     * Function: SortIcon
     * Path: frontend/src/pages/dashboard/LevyFairnessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const SortIcon = ({col}) => {
        if (unitSort !== col) return <ChevronUp className="w-3 h-3 text-muted-foreground/40"/>;
        return unitSortDir === "asc"
            ? <ChevronUp className="w-3 h-3 text-primary"/>
            : <ChevronDown className="w-3 h-3 text-primary"/>;
    };

    // ── Loading / empty states ─────────────────────────────────────────────────
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
                        <Scale className="w-14 h-14 text-muted-foreground mx-auto mb-4"/>
                        <h2 className="text-xl font-semibold mb-2">No Fairness Analysis Available</h2>
                        <p className="text-muted-foreground mb-6 text-sm">
                            The analysis needs to be computed first. This pulls from the Digital Twin (facilities,
                            assets)
                            and the levy database.
                        </p>
                        {canManage && (
                            <Button onClick={handleRecompute} disabled={recomputing}>
                                <RefreshCw className={`w-4 h-4 mr-2 ${recomputing ? "animate-spin" : ""}`}/>
                                {recomputing ? "Computing…" : "Generate Analysis"}
                            </Button>
                        )}
                    </CardContent>
                </Card>
            </div>
        );
    }

    const lbfi = data.lbfi;
    const subsidyMap = data.subsidy_map;
    const impact = data.impact_by_group || [];
    const computedAt = data.computed_at;

    // ── Render ─────────────────────────────────────────────────────────────────
    return (
        <div className="space-y-6">

            {/* Page header */}
            <div className="flex items-start justify-between flex-wrap gap-4">
                <div>
                    <Button
                        variant="ghost"
                        size="sm"
                        className="w-fit -ml-2 text-muted-foreground mb-1"
                        onClick={() => router.push("/intelligence/building")}
                    >
                        <ArrowLeft className="h-4 w-4 mr-1"/>
                        Back to Intelligence Hub
                    </Button>
                    {/* The description is computed (lot counts per group), so it is passed
                        as a node rather than a string. The IIFE is preserved verbatim — it
                        renders "." when there are no groups, which keeps the sentence
                        grammatical in the empty case. */}
                    <PageHeader
                        className="border-b-0 pb-0"
                        title="Levy Fairness Allocation Engine"
                        icon={<Scale className="h-5 w-5"/>}
                        description={
                            <>
                                Levy Benefit Fairness Index — Gini-style payment vs benefit gap
                                {(() => {
                                    const groups = data?.impact_by_group || [];
                                    // The API field is `lots`; reading `count` made this
                                    // reduce to 0 for every scheme, so the composition
                                    // sentence rendered as a bare "." and nobody saw it.
                                    const lotsOf = (g) => g.lots ?? g.count ?? 0;
                                    const total = groups.reduce((s, g) => s + lotsOf(g), 0);
                                    if (!total) return ".";
                                    const parts = groups
                                        .filter((g) => lotsOf(g))
                                        .map((g) => `${lotsOf(g)} ${g.group}${lotsOf(g) === 1 ? "" : "s"}`)
                                        .join(" + ");
                                    return ` across ${total} lots (${parts}).`;
                                })()}
                            </>
                        }
                    />
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                    <Button variant="outline" size="sm" onClick={load}><RefreshCw
                        className="w-4 h-4 mr-2"/>Refresh</Button>
                    {canManage && (
                        <Button size="sm" onClick={handleRecompute} disabled={recomputing}>
                            <RefreshCw className={`w-4 h-4 mr-2 ${recomputing ? "animate-spin" : ""}`}/>
                            {recomputing ? "Computing…" : "Regenerate Model"}
                        </Button>
                    )}
                </div>
            </div>

            {/* What-if / discussion-tool disclaimer — this model is a decision-support
                sandbox for Owners Corporation and owner discussion. It never writes to
                actual levy charges (unit_levy_ledger / annual_levies); those remain
                unit-entitlement-proportional by default under ACT UTMA 2011 s.6/s.78/s.89
                (or NSW SSMA 2015 s.81-83) unless and until a Special Resolution or
                tribunal order formally changes them — see "Legal Pathways" below. */}
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 flex items-start gap-2">
                <Info className="w-4 h-4 mt-0.5 flex-shrink-0"/>
                <p>
                    <strong>What-if model, not a real levy.</strong> This tool exists so the Owners Corporation and
                    owners can explore and discuss what a benefit-based allocation could look like — it never
                    changes what anyone is actually charged. By default, real levies are charged strictly in
                    proportion to registered Unit Entitlement. Turning an idea explored here into an actual charge
                    requires a Special Resolution (or tribunal order) under one of the "Legal Pathways" below.
                </p>
            </div>

            {/* The backend's own verdict on its inputs, rendered.
                `missing_inputs` has been returned and set into state since Phase 1 and
                nothing displayed it, so a result the service declined to stand behind
                still rendered as four confident KPI tiles. Naming each absent input is
                the point: an operator can act on "capital_items_unresolved", not on a
                greyed-out number. */}
            {failedEndpoints.length > 0 && (
                <div className="rounded-xl border border-rose-300 bg-rose-50 p-4">
                    <div className="flex items-start gap-3">
                        <AlertTriangle className="w-5 h-5 text-rose-700 flex-shrink-0 mt-0.5"/>
                        <div className="min-w-0">
                            <p className="font-semibold text-sm text-rose-900">
                                Some data did not load — sections below may be empty for that reason alone
                            </p>
                            {/* "Did not answer" and "answered with nothing" are different
                                facts and must not both render as an empty panel. */}
                            <p className="text-xs text-rose-800 mt-1 font-mono break-all">
                                {failedEndpoints.join(", ")}
                            </p>
                        </div>
                    </div>
                </div>
            )}

            {missingInputs.length > 0 && (
                <div className="rounded-xl border border-amber-300 bg-amber-50 p-4">
                    <div className="flex items-start gap-3">
                        <AlertTriangle className="w-5 h-5 text-amber-700 flex-shrink-0 mt-0.5"/>
                        <div className="min-w-0">
                            <p className="font-semibold text-sm text-amber-900">
                                These figures are incomplete — treat them as indicative, not as a position
                            </p>
                            <p className="text-xs text-amber-800 mt-1">
                                The fairness engine could not source every input it needs:
                            </p>
                            <ul className="mt-2 space-y-1">
                                {missingInputs.map((key) => (
                                    <li key={key} className="text-xs text-amber-900 flex items-start gap-2">
                                        <span className="mt-1.5 w-1 h-1 rounded-full bg-amber-700 flex-shrink-0"/>
                                        <span>{MISSING_INPUT_LABELS[ key ] || key}</span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    </div>
                </div>
            )}

            {/* Metrics Row */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="rounded-xl border p-4 bg-card shadow-sm">
                    <p className="text-xs font-medium text-muted-foreground uppercase">LBFI Score</p>
                    <div className={`text-3xl font-semibold mt-1 ${lbfiBand(lbfi?.current_score ?? 0).color}`}>
                        {lbfi?.current_score ?? "—"}<span
                        className="text-sm font-normal text-muted-foreground"> / 100</span>
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5">{lbfiBand(lbfi?.current_score ?? 0).label}</p>
                </div>
                <div className="rounded-xl border p-4 bg-card shadow-sm">
                    <p className="text-xs font-medium text-muted-foreground uppercase">Total Scheme Levy</p>
                    <div className="text-3xl font-semibold mt-1 text-primary">{aud(data?.total_budget)}</div>
                    <p className="text-xs text-muted-foreground mt-0.5">
                        {data?.financial_year ? `FY${data.financial_year} actual` : "Current levy year"}
                    </p>
                </div>
                <div className="rounded-xl border p-4 bg-card shadow-sm">
                    <p className="text-xs font-medium text-muted-foreground uppercase">Cross-Subsidy Flow</p>
                    <div className="text-3xl font-semibold mt-1 text-rose-600">
                        {aud(subsidyMap?.flows?.[ 0 ]?.amount ?? 0)}
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5">
                        {subsidyMap?.flows?.[ 0 ]
                            ? `${subsidyMap.flows[ 0 ].from} → ${subsidyMap.flows[ 0 ].to} / yr`
                            : "No measured cross-subsidy"}
                    </p>
                </div>
                <div className="rounded-xl border p-4 bg-card shadow-sm">
                    <p className="text-xs font-medium text-muted-foreground uppercase">Special Levy Risk</p>
                    <div
                        className={`text-3xl font-semibold mt-1 ${( data?.simulation?.special_levy_probability || 0 ) > 30 ? "text-rose-600" : "text-emerald-600"}`}>
                        {data?.simulation?.special_levy_probability || 0}%
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5">10yr probability</p>
                </div>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
                <TabsList className="flex-wrap h-auto gap-1">
                    <TabsTrigger value="overview" className="gap-1.5"><Eye
                        className="w-3.5 h-3.5"/>Overview</TabsTrigger>
                    <TabsTrigger value="impact" className="gap-1.5"><Users className="w-3.5 h-3.5"/>Unit
                        Impact {unitImpact.length > 0 &&
                            <Badge variant="secondary" className="ml-1 text-xs">{unitImpact.length}</Badge>}
                    </TabsTrigger>
                    <TabsTrigger value="facilities" className="gap-1.5"><Building
                        className="w-3.5 h-3.5"/>Facilities {facilities.length > 0 &&
                        <Badge variant="secondary" className="ml-1 text-xs">{facilities.length}</Badge>}</TabsTrigger>
                    <TabsTrigger value="groups" className="gap-1.5"><Layers
                        className="w-3.5 h-3.5"/>Groups</TabsTrigger>
                    <TabsTrigger value="simulation" className="gap-1.5"><BarChart3 className="w-3.5 h-3.5"/>Risk
                        Prediction</TabsTrigger>
                    <TabsTrigger value="budget" className="gap-1.5"><SlidersHorizontal className="w-3.5 h-3.5"/>Scenarios</TabsTrigger>
                    <TabsTrigger value="demo" className="gap-1.5"><BarChart3 className="w-3.5 h-3.5"/>Levy
                        History</TabsTrigger>
                    <TabsTrigger value="agm" className="gap-1.5"><FileText className="w-3.5 h-3.5"/>AGM
                        Report</TabsTrigger>
                    <TabsTrigger value="distribution" className="gap-1.5"><BarChart3 className="w-3.5 h-3.5"/>Distribution</TabsTrigger>
                    {canEdit &&
                        <TabsTrigger value="snapshots" className="gap-1.5"><Save className="w-3.5 h-3.5"/>Save Snapshot</TabsTrigger>}
                    {canEdit && <TabsTrigger value="audit" className="gap-1.5"><FileText className="w-3.5 h-3.5"/>Audit</TabsTrigger>}
                    <TabsTrigger value="cross-subsidy" className="gap-1.5"><Scale className="w-3.5 h-3.5"/>Cross-Subsidy</TabsTrigger>
                </TabsList>

                {/* ── OVERVIEW ── */}
                <TabsContent value="overview" className="space-y-6 mt-4">
                    <LbfiHero lbfi={lbfi} onRecompute={handleRecompute} canRecompute={canManage}
                              recomputing={recomputing} computedAt={computedAt}/>
                    {data?.confidence && (
                        <Suspense fallback={null}>
                            <ModelConfidenceBadge confidence={data.confidence}/>
                        </Suspense>
                    )}
                    {/* Class A/B split active banner */}
                    {splitStatus?.split_active && (
                        <div className="flex items-start gap-3 p-4 rounded-lg border border-primary/20 bg-primary/10">
                            <Layers className="w-5 h-5 text-primary mt-0.5 shrink-0"/>
                            <div className="flex-1 min-w-0">
                                <p className="font-semibold text-primary text-sm">Class A/B Scheme Split is
                                    active</p>
                                <p className="text-xs text-primary mt-0.5">
                                    This building operates separate levy pools for{" "}
                                    <span className="font-medium">{splitStatus.class_a?.class_name || "Class A"}</span>
                                    {" "}and{" "}
                                    <span className="font-medium">{splitStatus.class_b?.class_name || "Class B"}</span>.
                                    {" "}Levy calculations are performed independently per class.
                                    {canEdit && (
                                        <button
                                            className="ml-2 underline hover:text-primary"
                                            onClick={() => window.location.href = "/admin/scheme-classes"}
                                        >Manage split →</button>
                                    )}
                                </p>
                                {splitStatus.class_a && splitStatus.class_b && (
                                    <div className="flex flex-wrap gap-3 mt-2">
                    <span
                        className="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 border border-amber-200">
                      {splitStatus.class_a.class_name}: {splitStatus.class_a.unit_count ?? "?"} units
                        {splitStatus.class_a.total_uoe != null && ` · UOE ${splitStatus.class_a.total_uoe}`}
                    </span>
                                        <span
                                            className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
                      {splitStatus.class_b.class_name}: {splitStatus.class_b.unit_count ?? "?"} units
                                            {splitStatus.class_b.total_uoe != null && ` · UOE ${splitStatus.class_b.total_uoe}`}
                    </span>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                    <ExplainerBox buildingName={selectedBuilding?.name}/>
                    <ScenarioComparisonCard lbfi={lbfi} impact={impact}/>
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                        <div className="lg:col-span-2"><SubsidyFlowCard subsidyMap={subsidyMap}/></div>
                        <div><LegalPathwayCard/></div>
                    </div>
                </TabsContent>

                {/* ── IMPACT ── */}
                <TabsContent value="impact" className="space-y-5 mt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <Users className="w-5 h-5 text-primary"/> Per-Unit Levy Impact{unitImpact.length > 0 ? ` — All ${unitImpact.length} Lots` : ""}
                            </CardTitle>
                            <CardDescription>
                                What every individual lot would pay under the benefit-based model vs the current UE
                                scheme.
                                The <strong>same total levy is collected</strong> — only the distribution changes.
                                Sort by Change $ to see biggest movers.
                            </CardDescription>
                        </CardHeader>
                    </Card>

                    <UnitImpactSummary unitImpact={unitImpact}/>

                    {/* Filters */}
                    <div className="flex items-center gap-3 flex-wrap">
                        <div className="relative flex-1 min-w-48">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground"/>
                            <Input placeholder="Search by unit or owner name…" className="pl-9"
                                   value={unitSearch} onChange={(e) => {
                                setUnitSearch(e.target.value);
                                setUnitPage(1);
                            }}/>
                        </div>
                        <Select value={unitTypeFilter} onValueChange={(v) => {
                            setUnitTypeFilter(v);
                            setUnitPage(1);
                        }}>
                            <SelectTrigger className="w-44"><SelectValue placeholder="All types"/></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Lot Types</SelectItem>
                                {lotTypeOptions.map((opt) => (
                                    <SelectItem key={opt.value} value={opt.value}>
                                        {opt.label} ({opt.count})
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground ml-auto">
                            {filteredUnits.length} of {unitImpact.length} lots
                        </p>
                    </div>

                    {/* Unit table */}
                    <Card>
                        <CardContent className="p-0">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead className="cursor-pointer select-none"
                                                   onClick={() => toggleSort("unit_number")}>
                                            <div className="flex items-center gap-1">Unit <SortIcon col="unit_number"/>
                                            </div>
                                        </TableHead>
                                        <TableHead className="cursor-pointer select-none"
                                                   onClick={() => toggleSort("owner_name")}>
                                            <div className="flex items-center gap-1">Owner <SortIcon col="owner_name"/>
                                            </div>
                                        </TableHead>
                                        <TableHead>Type</TableHead>
                                        {splitStatus?.split_active && <TableHead>Class</TableHead>}
                                        <TableHead className="cursor-pointer select-none text-right"
                                                   onClick={() => toggleSort("entitlement")}>
                                            <div className="flex items-center gap-1 justify-end">UOE <SortIcon
                                                col="entitlement"/></div>
                                        </TableHead>
                                        <TableHead className="cursor-pointer select-none text-right"
                                                   onClick={() => toggleSort("current_levy")}>
                                            <div className="flex items-center gap-1 justify-end">Current Levy <SortIcon
                                                col="current_levy"/></div>
                                        </TableHead>
                                        <TableHead className="cursor-pointer select-none text-right"
                                                   onClick={() => toggleSort("fair_levy")}>
                                            <div className="flex items-center gap-1 justify-end">Benefit-Based <SortIcon
                                                col="fair_levy"/></div>
                                        </TableHead>
                                        <TableHead className="cursor-pointer select-none text-right"
                                                   onClick={() => toggleSort("change")}>
                                            <div className="flex items-center gap-1 justify-end">Change $ <SortIcon
                                                col="change"/></div>
                                        </TableHead>
                                        <TableHead className="cursor-pointer select-none text-right"
                                                   onClick={() => toggleSort("change_pct")}>
                                            <div className="flex items-center gap-1 justify-end">Change % <SortIcon
                                                col="change_pct"/></div>
                                        </TableHead>
                                        <TableHead>Impact</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {pagedUnits.length === 0 ? (
                                        <TableRow>
                                            <TableCell colSpan={splitStatus?.split_active ? 10 : 9}
                                                       className="text-center py-12 text-muted-foreground">
                                                {unitImpact.length === 0 ? "Run the analysis first." : "No units match your search."}
                                            </TableCell>
                                        </TableRow>
                                    ) : pagedUnits.map((u) => {
                                        const chg = u.change;
                                        const pctVal = u.change_pct;
                                        const isMe = activeUnit && u.unit_number === activeUnit;
                                        return (
                                            <TableRow key={u.unit_number}
                                                      className={`cursor-pointer hover:bg-muted ${isMe ? "bg-primary/10 border-l-2 border-l-primary" : ""}`}
                                                      onClick={() => {
                                                          setExplainUnit(u);
                                                          setExplainOpen(true);
                                                      }}
                                            >
                                                <TableCell>
                                                    <span
                                                        className="font-mono font-semibold text-sm">{u.unit_number}</span>
                                                    {isMe &&
                                                        <Badge className="ml-1 text-xs" variant="secondary">You</Badge>}
                                                </TableCell>
                                                <TableCell className="text-sm">{u.owner_name || "—"}</TableCell>
                                                <TableCell>
                                                    <Badge variant="outline" className={`text-xs ${
                                                        u.unit_type?.toLowerCase() === "apartment" ? "text-amber-700 border-amber-300" :
                                                            u.unit_type?.toLowerCase() === "townhouse" ? "text-primary border-primary/20" : ""}`}>
                                                        {u.unit_type || "—"}
                                                    </Badge>
                                                </TableCell>
                                                {splitStatus?.split_active && (
                                                    <TableCell>
                                                        {u.scheme_class ? (
                                                            <Badge variant="outline" className={`text-xs ${
                                                                u.scheme_class === "A" ? "text-primary border-primary/20 bg-primary/10" :
                                                                    u.scheme_class === "B" ? "text-primary border-primary/20 bg-primary/10" : ""}`}>
                                                                Class {u.scheme_class}
                                                            </Badge>
                                                        ) : (
                                                            <span className="text-xs text-muted-foreground">—</span>
                                                        )}
                                                    </TableCell>
                                                )}
                                                <TableCell className="text-right text-sm">{u.entitlement}</TableCell>
                                                <TableCell
                                                    className="text-right text-sm font-medium">{aud(u.current_levy)}</TableCell>
                                                <TableCell
                                                    className="text-right text-sm font-medium">{aud(u.fair_levy)}</TableCell>
                                                <TableCell
                                                    className={`text-right font-semibold text-sm ${chg < -0.5 ? "text-emerald-600" : chg > 0.5 ? "text-rose-600" : "text-muted-foreground"}`}>
                                                    {Math.abs(chg) < 0.5 ? "—" : ( chg < 0 ? "−" : "+" ) + aud(Math.abs(chg)).replace("$", "")}
                                                </TableCell>
                                                <TableCell
                                                    className={`text-right font-semibold text-sm ${pctVal < -0.1 ? "text-emerald-600" : pctVal > 0.1 ? "text-rose-600" : "text-muted-foreground"}`}>
                                                    {Math.abs(pctVal) < 0.1 ? "—" : pct(pctVal)}
                                                </TableCell>
                                                <TableCell>
                                                    {chg < -0.5 ? (
                                                        <Badge
                                                            className="text-xs bg-emerald-100 text-emerald-800 border-emerald-300">
                                                            <TrendingDown className="w-3 h-3 mr-1"/>Decrease
                                                        </Badge>
                                                    ) : chg > 0.5 ? (
                                                        <Badge
                                                            className="text-xs bg-rose-100 text-rose-800 border-rose-300">
                                                            <TrendingUp className="w-3 h-3 mr-1"/>Increase
                                                        </Badge>
                                                    ) : (
                                                        <Badge variant="outline" className="text-xs text-muted-foreground">
                                                            <Minus className="w-3 h-3 mr-1"/>Neutral
                                                        </Badge>
                                                    )}
                                                </TableCell>
                                            </TableRow>
                                        );
                                    })}
                                </TableBody>
                            </Table>
                        </CardContent>
                    </Card>

                    {/* Pagination */}
                    {totalPages > 1 && (
                        <div className="flex items-center justify-center gap-2">
                            <Button variant="outline" size="sm" disabled={unitPage === 1}
                                    onClick={() => setUnitPage((p) => p - 1)}>Previous</Button>
                            <span className="text-sm text-muted-foreground">
                Page {unitPage} of {totalPages} ({filteredUnits.length} lots)
              </span>
                            <Button variant="outline" size="sm" disabled={unitPage === totalPages}
                                    onClick={() => setUnitPage((p) => p + 1)}>Next</Button>
                        </div>
                    )}
                </TabsContent>

                {/* ── FACILITIES ── */}
                <TabsContent value="facilities" className="space-y-5 mt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <Building className="w-5 h-5 text-primary"/> Facility Cost Centres
                            </CardTitle>
                            <CardDescription>
                                These facility cost centres determine how the levy is divided.{" "}
                                <strong>All-Lots (Global)</strong> facilities are shared by UOE.{" "}
                                <strong>Type-specific</strong> facilities are allocated only to that unit type,
                                eliminating cross-subsidies.
                            </CardDescription>
                        </CardHeader>
                    </Card>

                    <TierSummaryRow facilities={facilities} groupsById={groupsById}/>

                    {/* Explainer */}
                    <Card className="border-amber-200 bg-amber-50">
                        <CardContent className="pt-4 text-xs text-amber-900 space-y-1">
                            <p className="font-semibold">Three-Tier Budget Model</p>
                            <p><strong>Global (All Owners):</strong> Costs shared by all owners in UOE proportion —
                                insurance, management fees, external landscaping, shared driveway, general maintenance.
                            </p>
                            <p><strong>Apartment-Only:</strong> Costs borne only by Apartment owners — lifts, high-rise
                                fire systems, internal corridor cleaning, basement ventilation, apartment intercom.</p>
                            <p><strong>Townhouse-Only:</strong> Costs borne only by Townhouse owners —
                                townhouse-specific pathways, perimeter fencing, garden maintenance, private utility
                                connections.</p>
                            <p className="mt-1 text-amber-700">Implementation requires an <strong>Exclusive Use
                                By-Law</strong> passed by Special Resolution (75% of vote value).</p>
                        </CardContent>
                    </Card>

                    {/* Toolbar */}
                    <div className="flex items-center gap-3 flex-wrap">
                        <div className="relative flex-1 min-w-48">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground"/>
                            <Input placeholder="Search facilities…" className="pl-9"
                                   value={facSearch} onChange={(e) => setFacSearch(e.target.value)}/>
                        </div>
                    </div>

                    {/* Facility table */}
                    <Card>
                        <CardContent className="p-0">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Facility</TableHead>
                                        <TableHead>Annual Cost</TableHead>
                                        <TableHead>Benefit Group</TableHead>
                                        <TableHead>Allocation Driver</TableHead>
                                        <TableHead>Status</TableHead>
                                        {canEdit && <TableHead className="text-right">Actions</TableHead>}
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {facilities
                                        .filter((f) => !facSearch || f.facility_name?.toLowerCase().includes(facSearch.toLowerCase()))
                                        .map((fac) => {
                                            const bgName = groupsById[ fac.benefit_group_id ] || fac.benefit_group_id || "ALL_LOTS";
                                            const isApt = bgName.toUpperCase().includes("APARTMENT");
                                            const isTH = bgName.toUpperCase().includes("TOWNHOUSE");
                                            const tierColor = isApt
                                                ? "bg-amber-100 text-amber-800"
                                                : isTH
                                                    ? "bg-primary/10 text-primary"
                                                    : "bg-primary/10 text-primary";
                                            const isEditingThis = editingFacId === fac.facility_id;
                                            return (
                                                <TableRow key={fac.facility_id}
                                                          className={isEditingThis ? "ring-2 ring-primary/20 bg-primary/5" : ""}>
                                                    <TableCell className="font-medium">
                                                        {isEditingThis ? (
                                                            <Input value={facDraft.facility_name}
                                                                   onChange={e => setFacDraft(d => ( {
                                                                       ...d,
                                                                       facility_name: e.target.value
                                                                   } ))} className="h-8 w-full text-sm"/>
                                                        ) : fac.facility_name}
                                                    </TableCell>
                                                    <TableCell className="font-semibold">
                                                        {isEditingThis ? (
                                                            <Input type="number" min="0" step="100"
                                                                   value={facDraft.annual_cost}
                                                                   onChange={e => setFacDraft(d => ( {
                                                                       ...d,
                                                                       annual_cost: e.target.value
                                                                   } ))}
                                                                   className="h-8 w-28 text-sm"/>
                                                        ) : aud(fac.annual_cost)}
                                                    </TableCell>
                                                    <TableCell>
                                                        {isEditingThis ? (
                                                            <select value={facDraft.benefit_group_id}
                                                                    onChange={e => setFacDraft(d => ( {
                                                                        ...d,
                                                                        benefit_group_id: e.target.value
                                                                    } ))}
                                                                    className="h-8 rounded border border-input bg-background px-2 text-sm">
                                                                <option value="">ALL_LOTS</option>
                                                                {groups.map(g => <option key={g.id}
                                                                                         value={g.id}>{g.name}</option>)}
                                                            </select>
                                                        ) : <Badge className={`text-xs ${tierColor}`}>{bgName}</Badge>}
                                                    </TableCell>
                                                    <TableCell>
                                                        {isEditingThis ? (
                                                            <select value={facDraft.allocation_driver}
                                                                    onChange={e => setFacDraft(d => ( {
                                                                        ...d,
                                                                        allocation_driver: e.target.value
                                                                    } ))}
                                                                    className="h-8 rounded border border-input bg-background px-2 text-sm">
                                                                {Object.entries(DRIVER_LABELS).map(([k, v]) => <option
                                                                    key={k} value={k}>{v}</option>)}
                                                            </select>
                                                        ) : <span
                                                            className="text-sm text-muted-foreground">{DRIVER_LABELS[ fac.allocation_driver ] || fac.allocation_driver}</span>}
                                                    </TableCell>
                                                    <TableCell>{fac.enabled !== false ? <Badge
                                                            className="bg-emerald-100 text-emerald-800">Active</Badge> :
                                                        <Badge variant="secondary">Disabled</Badge>}</TableCell>
                                                    {canEdit && (
                                                        <TableCell className="text-right">
                                                            {isEditingThis ? (
                                                                <div className="flex items-center justify-end gap-1">
                                                                    <Button size="icon" variant="ghost"
                                                                            className="h-7 w-7 text-green-600"
                                                                            title="Save"
                                                                            disabled={savingFac}
                                                                            onClick={() => handleSaveFacility(fac.facility_id)}>
                                                                        <Check className="w-4 h-4"/>
                                                                    </Button>
                                                                    <Button size="icon" variant="ghost"
                                                                            className="h-7 w-7 text-muted-foreground"
                                                                            title="Cancel"
                                                                            onClick={() => setEditingFacId(null)}>
                                                                        <X className="w-4 h-4"/>
                                                                    </Button>
                                                                </div>
                                                            ) : (
                                                                <Button size="icon" variant="ghost"
                                                                        className="h-7 w-7 text-muted-foreground hover:text-primary"
                                                                        title="Edit facility"
                                                                        onClick={() => handleEditFacility(fac)}>
                                                                    <Pencil className="w-3.5 h-3.5"/>
                                                                </Button>
                                                            )}
                                                        </TableCell>
                                                    )}
                                                </TableRow>
                                            );
                                        })
                                    }
                                </TableBody>
                            </Table>
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── GROUPS ── */}
                <TabsContent value="groups" className="space-y-4 mt-4">
                    <Card>
                        <CardHeader>
                            <div className="flex items-center justify-between">
                                <div>
                                    <CardTitle className="flex items-center gap-2"><Layers
                                        className="w-5 h-5 text-primary"/>Benefit Groups</CardTitle>
                                    <CardDescription className="mt-1">
                                        Define which lots receive each facility's costs. Unit matching supports flexible
                                        prefixes and ranges —
                                        e.g. prefix <code className="bg-muted px-1 rounded text-xs">UA</code> for
                                        apartments,{" "}
                                        <code className="bg-muted px-1 rounded text-xs">TH</code> for townhouses,
                                        or{" "}
                                        <code className="bg-muted px-1 rounded text-xs">U</code> with range <code
                                        className="bg-muted px-1 rounded text-xs">U001–U070</code> for buildings with
                                        sequential numbering.
                                        Explicit lot numbers always take priority.
                                    </CardDescription>
                                </div>
                                {canEdit && (
                                    <Button size="sm" onClick={() => setAddGroupOpen(true)}
                                            className="gap-1.5 shrink-0">
                                        <Plus className="w-4 h-4"/>Add Group
                                    </Button>
                                )}
                            </div>
                        </CardHeader>
                    </Card>

                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {groups.map((bg) => {
                            const gtype = bg.group_type || ( bg.name?.toUpperCase().includes("APARTMENT") ? "apartment"
                                : bg.name?.toUpperCase().includes("TOWNHOUSE") ? "townhouse" : "global" );
                            const borderClass = gtype === "apartment" ? "border-amber-200 bg-amber-50"
                                : gtype === "townhouse" ? "border-primary/20 bg-primary/10"
                                    : gtype === "custom" ? "border-primary/20 bg-primary/10"
                                        : "border-primary/20 bg-primary/10";
                            const isEditing = editingGroupId === bg.id;
                            const preview = groupPreview[ bg.id ];

                            return (
                                <Card key={bg.id}
                                      className={`${borderClass} transition-shadow ${isEditing ? "shadow-lg ring-2 ring-primary/30" : ""}`}>
                                    <CardHeader className="pb-2">
                                        <div className="flex justify-between items-start gap-2">
                                            {isEditing ? (
                                                <Input value={groupDraft.name} onChange={e => setGroupDraft(d => ( {
                                                    ...d,
                                                    name: e.target.value
                                                } ))}
                                                       className="h-7 text-sm font-semibold" placeholder="Group name"/>
                                            ) : (
                                                <CardTitle className="text-base">{bg.name}</CardTitle>
                                            )}
                                            <div className="flex gap-1 shrink-0">
                                                <Badge variant="outline" className="text-xs capitalize">{gtype}</Badge>
                                                {canEdit && !isEditing && (
                                                    <>
                                                        <Tooltip>
                                                            <TooltipTrigger asChild>
                                                                <Button
                                                                    variant="ghost"
                                                                    size="icon"
                                                                    className="h-6 w-6"
                                                                    aria-label="Edit group"
                                                                    onClick={() => handleEditGroup(bg)}
                                                                >
                                                                    <motion.div whileTap={{scale: 0.95}}>
                                                                        <Edit2 className="w-3.5 h-3.5"/>
                                                                    </motion.div>
                                                                </Button>
                                                            </TooltipTrigger>
                                                            <TooltipContent>Edit group</TooltipContent>
                                                        </Tooltip>

                                                        {canDelete && (
                                                            <Tooltip>
                                                                <TooltipTrigger asChild>
                                                                    <Button
                                                                        variant="ghost"
                                                                        size="icon"
                                                                        className="h-6 w-6 text-rose-500 hover:text-rose-700"
                                                                        aria-label="Delete group"
                                                                        disabled={deletingGroupId === bg.id}
                                                                        onClick={() => handleDeleteGroup(bg.id)}
                                                                    >
                                                                        <motion.div whileTap={{scale: 0.95}}>
                                                                            <Trash2 className="w-3.5 h-3.5"/>
                                                                        </motion.div>
                                                                    </Button>
                                                                </TooltipTrigger>
                                                                <TooltipContent>Delete group</TooltipContent>
                                                            </Tooltip>
                                                        )}
                                                    </>
                                                )}
                                                {canEdit && isEditing && (
                                                    <>
                                                        <Button variant="ghost" size="icon"
                                                                className="h-6 w-6 text-emerald-600"
                                                                title="Save" disabled={savingGroup}
                                                                onClick={() => handleSaveGroup(bg.id)}>
                                                            <Save className="w-3.5 h-3.5"/>
                                                        </Button>
                                                        <Button variant="ghost" size="icon" className="h-6 w-6"
                                                                title="Cancel" onClick={() => setEditingGroupId(null)}>
                                                            <X className="w-3.5 h-3.5"/>
                                                        </Button>
                                                    </>
                                                )}
                                            </div>
                                        </div>
                                        {isEditing ? (
                                            <Input value={groupDraft.description} onChange={e => setGroupDraft(d => ( {
                                                ...d,
                                                description: e.target.value
                                            } ))}
                                                   className="h-7 text-xs mt-1" placeholder="Description (optional)"/>
                                        ) : (
                                            <CardDescription className="text-xs">{bg.description}</CardDescription>
                                        )}
                                    </CardHeader>

                                    <CardContent className="space-y-2 pt-0">
                                        {isEditing ? (
                                            <div className="space-y-2 text-xs">
                                                {/* Group type */}
                                                <div className="flex items-center gap-2">
                                                    <Label className="w-28 shrink-0 text-muted-foreground">Type</Label>
                                                    <Select value={groupDraft.group_type}
                                                            onValueChange={v => setGroupDraft(d => ( {
                                                                ...d,
                                                                group_type: v
                                                            } ))}>
                                                        <SelectTrigger
                                                            className="h-7 text-xs"><SelectValue/></SelectTrigger>
                                                        <SelectContent>
                                                            <SelectItem value="global">Global (All Lots)</SelectItem>
                                                            <SelectItem value="apartment">Apartment</SelectItem>
                                                            <SelectItem value="townhouse">Townhouse</SelectItem>
                                                            <SelectItem value="custom">Custom</SelectItem>
                                                        </SelectContent>
                                                    </Select>
                                                </div>
                                                {/* Allocation driver */}
                                                <div className="flex items-center gap-2">
                                                    <Label
                                                        className="w-28 shrink-0 text-muted-foreground">Driver</Label>
                                                    <Select value={groupDraft.allocation_driver}
                                                            onValueChange={v => setGroupDraft(d => ( {
                                                                ...d,
                                                                allocation_driver: v
                                                            } ))}>
                                                        <SelectTrigger
                                                            className="h-7 text-xs"><SelectValue/></SelectTrigger>
                                                        <SelectContent>
                                                            {Object.entries(DRIVER_LABELS).map(([k, v]) => <SelectItem
                                                                key={k} value={k}>{v}</SelectItem>)}
                                                        </SelectContent>
                                                    </Select>
                                                </div>
                                                {/* Unit prefixes */}
                                                <div className="flex items-center gap-2">
                                                    <Label
                                                        className="w-28 shrink-0 text-muted-foreground">Prefixes</Label>
                                                    <Input value={groupDraft.unit_prefixes}
                                                           onChange={e => setGroupDraft(d => ( {
                                                               ...d,
                                                               unit_prefixes: e.target.value
                                                           } ))}
                                                           className="h-7 text-xs"
                                                           placeholder="UA, TH (comma-separated)"/>
                                                </div>
                                                {/* Number range */}
                                                <div className="flex items-center gap-2">
                                                    <Label className="w-28 shrink-0 text-muted-foreground">Range</Label>
                                                    <Input value={groupDraft.unit_number_range_min}
                                                           onChange={e => setGroupDraft(d => ( {
                                                               ...d,
                                                               unit_number_range_min: e.target.value
                                                           } ))}
                                                           className="h-7 text-xs w-20" placeholder="Min e.g. U001"/>
                                                    <span className="text-muted-foreground">–</span>
                                                    <Input value={groupDraft.unit_number_range_max}
                                                           onChange={e => setGroupDraft(d => ( {
                                                               ...d,
                                                               unit_number_range_max: e.target.value
                                                           } ))}
                                                           className="h-7 text-xs w-20" placeholder="Max e.g. U070"/>
                                                </div>
                                                {/* Explicit lot numbers */}
                                                <div className="flex items-start gap-2">
                                                    <Label className="w-28 shrink-0 text-muted-foreground mt-1">Explicit
                                                        Lots</Label>
                                                    <Input value={groupDraft.lot_numbers}
                                                           onChange={e => setGroupDraft(d => ( {
                                                               ...d,
                                                               lot_numbers: e.target.value
                                                           } ))}
                                                           className="h-7 text-xs"
                                                           placeholder="UA001, TH003, ... (overrides prefix)"/>
                                                </div>
                                                <p className="text-xs text-muted-foreground pt-1">
                                                    Priority: explicit lots → prefix+range → name fallback → all lots
                                                </p>
                                            </div>
                                        ) : (
                                            <>
                                                <div className="flex justify-between text-xs">
                                                    <span className="text-muted-foreground">Driver:</span>
                                                    <span
                                                        className="font-medium">{DRIVER_LABELS[ bg.allocation_driver ] || bg.allocation_rule?.allocation_type || "Unit Entitlement"}</span>
                                                </div>
                                                {bg.unit_prefixes?.length > 0 && (
                                                    <div className="flex justify-between text-xs">
                                                        <span className="text-muted-foreground">Prefixes:</span>
                                                        <span
                                                            className="font-medium font-mono">{bg.unit_prefixes.join(", ")}</span>
                                                    </div>
                                                )}
                                                {bg.unit_number_range && (
                                                    <div className="flex justify-between text-xs">
                                                        <span className="text-muted-foreground">Range:</span>
                                                        <span
                                                            className="font-medium font-mono">{bg.unit_number_range.min || "—"} – {bg.unit_number_range.max || "—"}</span>
                                                    </div>
                                                )}
                                                <div className="flex justify-between text-xs">
                                                    <span className="text-muted-foreground">Explicit lots:</span>
                                                    <span
                                                        className="font-medium">{bg.lot_numbers?.length > 0 ? `${bg.lot_numbers.length} specified` : "None"}</span>
                                                </div>
                                                {/* Preview section */}
                                                {preview ? (
                                                    <div className="mt-2 p-2 bg-card/60 rounded border text-xs">
                                                        <span
                                                            className="font-semibold text-primary">{preview.count} unit{preview.count !== 1 ? "s" : ""} matched</span>
                                                        {preview.count > 0 && preview.count <= 20 && (
                                                            <p className="text-muted-foreground mt-0.5 font-mono">{preview.matched_units.join(", ")}</p>
                                                        )}
                                                        {preview.count > 20 && (
                                                            <p className="text-muted-foreground mt-0.5 font-mono">{preview.matched_units.slice(0, 10).join(", ")} +{preview.count - 10} more</p>
                                                        )}
                                                    </div>
                                                ) : (
                                                    <Button variant="outline" size="sm"
                                                            className="h-6 text-xs mt-1 w-full gap-1"
                                                            onClick={() => handlePreviewGroup(bg.id)}>
                                                        <Eye className="w-3 h-3"/>Preview matched units
                                                    </Button>
                                                )}
                                            </>
                                        )}
                                    </CardContent>
                                </Card>
                            );
                        })}

                        {/* Empty state */}
                        {groups.length === 0 && (
                            <div className="col-span-3 text-center py-12 text-muted-foreground">
                                <Layers className="w-10 h-10 mx-auto mb-3 opacity-30"/>
                                <p className="text-sm">No benefit groups defined yet.</p>
                                {canEdit &&
                                    <p className="text-xs mt-1">Click <strong>Add Group</strong> to create the first
                                        one.</p>}
                            </div>
                        )}
                    </div>

                    {/* ── Add Group Dialog ── */}
                    <Dialog open={addGroupOpen} onOpenChange={setAddGroupOpen}>
                        <DialogContent className="max-w-lg">
                            <DialogHeader>
                                <DialogTitle className="flex items-center gap-2"><Plus className="w-4 h-4"/>New Benefit
                                    Group</DialogTitle>
                                <DialogDescription>
                                    Define a group of lots that share a set of facility costs. Use unit prefixes for
                                    flexible matching
                                    (e.g. <code className="bg-muted px-1 rounded">UA</code> for apartments, <code
                                    className="bg-muted px-1 rounded">U</code> with range for sequential numbering
                                    schemes).
                                </DialogDescription>
                            </DialogHeader>
                            <div className="space-y-3 py-2">
                                <div className="grid grid-cols-2 gap-3">
                                    <div className="space-y-1">
                                        <Label className="text-xs">Group Name *</Label>
                                        <Input value={newGroupDraft.name}
                                               onChange={e => setNewGroupDraft(d => ( {...d, name: e.target.value} ))}
                                               placeholder="e.g. APARTMENTS_ONLY" className="h-8 text-sm"/>
                                    </div>
                                    <div className="space-y-1">
                                        <Label className="text-xs">Type</Label>
                                        <Select value={newGroupDraft.group_type}
                                                onValueChange={v => setNewGroupDraft(d => ( {...d, group_type: v} ))}>
                                            <SelectTrigger className="h-8 text-sm"><SelectValue/></SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="global">Global (All Lots)</SelectItem>
                                                <SelectItem value="apartment">Apartment</SelectItem>
                                                <SelectItem value="townhouse">Townhouse</SelectItem>
                                                <SelectItem value="custom">Custom</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs">Description</Label>
                                    <Input value={newGroupDraft.description} onChange={e => setNewGroupDraft(d => ( {
                                        ...d,
                                        description: e.target.value
                                    } ))}
                                           placeholder="Brief description of who this group covers"
                                           className="h-8 text-sm"/>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs">Allocation Driver</Label>
                                    <Select value={newGroupDraft.allocation_driver}
                                            onValueChange={v => setNewGroupDraft(d => ( {
                                                ...d,
                                                allocation_driver: v
                                            } ))}>
                                        <SelectTrigger className="h-8 text-sm"><SelectValue/></SelectTrigger>
                                        <SelectContent>
                                            {Object.entries(DRIVER_LABELS).map(([k, v]) => <SelectItem key={k}
                                                                                                       value={k}>{v}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <Separator/>
                                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Unit
                                    Matching Rules</p>
                                <div className="space-y-1">
                                    <Label className="text-xs">Unit Prefixes <span
                                        className="text-muted-foreground">(comma-separated)</span></Label>
                                    <Input value={newGroupDraft.unit_prefixes} onChange={e => setNewGroupDraft(d => ( {
                                        ...d,
                                        unit_prefixes: e.target.value
                                    } ))}
                                           placeholder="UA  or  TH  or  U" className="h-8 text-sm font-mono"/>
                                    <p className="text-xs text-muted-foreground">e.g. <code>UA</code> matches
                                        UA001–UA087. Multiple: <code>UA, UB</code></p>
                                </div>
                                <div className="grid grid-cols-2 gap-3">
                                    <div className="space-y-1">
                                        <Label className="text-xs">Range Min <span
                                            className="text-muted-foreground">(optional)</span></Label>
                                        <Input value={newGroupDraft.unit_number_range_min}
                                               onChange={e => setNewGroupDraft(d => ( {
                                                   ...d,
                                                   unit_number_range_min: e.target.value
                                               } ))}
                                               placeholder="e.g. U001" className="h-8 text-sm font-mono"/>
                                    </div>
                                    <div className="space-y-1">
                                        <Label className="text-xs">Range Max <span
                                            className="text-muted-foreground">(optional)</span></Label>
                                        <Input value={newGroupDraft.unit_number_range_max}
                                               onChange={e => setNewGroupDraft(d => ( {
                                                   ...d,
                                                   unit_number_range_max: e.target.value
                                               } ))}
                                               placeholder="e.g. U070" className="h-8 text-sm font-mono"/>
                                    </div>
                                </div>
                                <div className="space-y-1">
                                    <Label className="text-xs">Explicit Lot Numbers <span
                                        className="text-muted-foreground">(overrides prefix — comma-separated)</span></Label>
                                    <Input value={newGroupDraft.lot_numbers} onChange={e => setNewGroupDraft(d => ( {
                                        ...d,
                                        lot_numbers: e.target.value
                                    } ))}
                                           placeholder="UA001, UA002, TH003 (optional — leave blank to use prefix)"
                                           className="h-8 text-sm font-mono"/>
                                </div>
                                <div className="rounded-md bg-primary/10 border border-primary/20 p-2 text-xs text-primary">
                                    <strong>Matching priority:</strong> Explicit lots → Prefix + range → Name-based
                                    fallback → All lots
                                </div>
                            </div>
                            <DialogFooter>
                                <Button variant="outline" onClick={() => setAddGroupOpen(false)}>Cancel</Button>
                                <Button onClick={handleCreateGroup}
                                        disabled={savingNewGroup || !newGroupDraft.name.trim()}>
                                    {savingNewGroup ? "Creating…" : "Create Group"}
                                </Button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>
                </TabsContent>

                {/* ── SIMULATION ── */}
                <TabsContent value="simulation" className="space-y-6 mt-4">
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                        <Card className="lg:col-span-2">
                            <CardHeader><CardTitle>10-Year Reserve Projection (Monte Carlo)</CardTitle>
                                <CardDescription>1,000 simulations with random-walk inflation (2–5%), capital overruns
                                    (±15%), and maintenance spikes.</CardDescription>
                            </CardHeader>
                            <CardContent className="h-80">
                                <ResponsiveContainer width="100%" height="100%">
                                    <AreaChart
                                        data={data?.simulation?.distribution?.map((v, i) => ( {x: i, reserve: v} ))}>
                                        <CartesianGrid strokeDasharray="3 3"/>
                                        <XAxis dataKey="x" hide/>
                                        <YAxis tickFormatter={(v) => `$${( v / 1000 ).toFixed(0)}k`}/>
                                        <RTooltip formatter={(v) => aud(v)}/>
                                        <Area type="monotone" dataKey="reserve" stroke="#8884d8" fill="#8884d8"
                                              fillOpacity={0.3}/>
                                    </AreaChart>
                                </ResponsiveContainer>
                            </CardContent>
                        </Card>

                        <Card>
                            <CardHeader><CardTitle>Predictive Risk Summary</CardTitle></CardHeader>
                            <CardContent className="space-y-6">
                                <div className="text-center space-y-2">
                                    <p className="text-sm text-muted-foreground uppercase font-semibold">Special Levy
                                        Probability</p>
                                    <div
                                        className={`text-5xl font-semibold ${data?.simulation?.special_levy_probability > 30 ? "text-rose-600" : "text-emerald-600"}`}>
                                        {data?.simulation?.special_levy_probability}%
                                    </div>
                                    <p className="text-xs text-muted-foreground">over 10 years</p>
                                </div>
                                <Separator/>
                                <div className="space-y-3">
                                    <div className="flex justify-between text-sm">
                                        <span className="text-muted-foreground">P50 (Median Reserve)</span>
                                        <span className="font-bold">{aud(data?.simulation?.p50)}</span>
                                    </div>
                                    <div className="flex justify-between text-sm">
                                        <span className="text-muted-foreground">P90 (Risk Level)</span>
                                        <span className="font-bold text-orange-600">{aud(data?.simulation?.p90)}</span>
                                    </div>
                                    <div className="flex justify-between text-sm">
                                        <span className="text-muted-foreground">P95 (Crisis Level)</span>
                                        <span className="font-bold text-rose-600">{aud(data?.simulation?.p95)}</span>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    </div>
                </TabsContent>

                {/* ── SCENARIOS ── */}
                <TabsContent value="budget" className="space-y-4 mt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2"><SlidersHorizontal
                                className="w-5 h-5 text-primary"/>Transition Modelling</CardTitle>
                            <CardDescription>
                                Use these controls to model a gradual transition to fairer levies.
                                The <strong>% cap</strong> limits how much any individual lot's levy can rise in a
                                single year as a proportion of their current levy.
                                The <strong>$ cap</strong> is an absolute ceiling per lot per year. The tighter of the
                                two limits applies.
                                A 10% cap / $1,000 limit allows a phased 3–5 year transition while protecting owners
                                from sudden increases.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-8">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-12">
                                <div className="space-y-4">
                                    <div className="flex justify-between items-center">
                                        <Label className="font-semibold">Max Annual Increase per Lot (%)</Label>
                                        <span className="font-semibold text-primary text-lg">{maxChangePct}%</span>
                                    </div>
                                    <Slider value={[maxChangePct]} onValueChange={([v]) => setMaxChangePct(v)} max={50}
                                            step={1}/>
                                    <div
                                        className="rounded-lg bg-muted border p-3 text-xs text-muted-foreground space-y-1">
                                        <p><strong>What this means:</strong> No lot will pay more than {maxChangePct}%
                                            above its current annual levy in any single year.</p>
                                        <p>Example: A townhouse currently paying <strong>$5,200/yr</strong> will
                                            increase by at most <strong>{aud(5200 * maxChangePct / 100)}/yr</strong> →
                                            new levy ≤ <strong>{aud(5200 + 5200 * maxChangePct / 100)}</strong> in Year
                                            1.</p>
                                        <p>At {maxChangePct}%, a full transition takes
                                            approximately <strong>{maxChangePct > 0 ? Math.ceil(100 / maxChangePct) : "∞"} year{Math.ceil(100 / maxChangePct) !== 1 ? "s" : ""}</strong>.
                                        </p>
                                    </div>
                                </div>

                                <div className="space-y-4">
                                    <div className="flex justify-between items-center">
                                        <Label className="font-semibold">Max Annual Increase per Lot ($)</Label>
                                        <span className="font-semibold text-primary text-lg">{aud(maxChangeAmt)}</span>
                                    </div>
                                    <Slider value={[maxChangeAmt]} onValueChange={([v]) => setMaxChangeAmt(v)}
                                            max={5000} step={100}/>
                                    <div
                                        className="rounded-lg bg-muted border p-3 text-xs text-muted-foreground space-y-1">
                                        <p><strong>What this means:</strong> Regardless of the % cap, no lot's levy
                                            rises by more than <strong>{aud(maxChangeAmt)}</strong> in a single year in
                                            absolute dollar terms.</p>
                                        <p>The <em>lower</em> of the % and $ caps always applies — this protects owners
                                            with large absolute levies from outsized dollar increases.</p>
                                        <p>At {aud(maxChangeAmt)}/yr cap, a lot with a <strong>$2,000</strong> fairness
                                            gap transitions
                                            over <strong>{maxChangeAmt > 0 ? Math.ceil(2000 / maxChangeAmt) : "∞"} year{Math.ceil(2000 / maxChangeAmt) !== 1 ? "s" : ""}</strong>.
                                        </p>
                                    </div>
                                </div>
                            </div>

                            <div className="flex justify-center">
                                <Button size="lg" onClick={handleRecompute} disabled={recomputing} className="gap-2">
                                    <RefreshCw className={`w-4 h-4 ${recomputing ? "animate-spin" : ""}`}/>
                                    {recomputing ? "Computing…" : "Run Transition Scenario"}
                                </Button>
                            </div>

                            {/* Per-group transition impact table */}
                            {( impact || [] ).length > 0 && (
                                <div className="space-y-3">
                                    <p className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Year-by-Year
                                        Group Impact</p>
                                    <div className="overflow-x-auto rounded-xl border">
                                        <Table>
                                            <TableHeader>
                                            <TableRow>
                                                <TableHead className="text-left px-4 py-2 font-semibold text-muted-foreground">Group</TableHead>
                                                <TableHead className="text-right px-4 py-2 font-semibold text-muted-foreground">Lots</TableHead>
                                                <TableHead className="text-right px-4 py-2 font-semibold text-muted-foreground">Current
                                                    Total
                                                </TableHead>
                                                <TableHead className="text-right px-4 py-2 font-semibold text-muted-foreground">Current
                                                    / Lot
                                                </TableHead>
                                                {[1, 2, 3, 4].map(yr => (
                                                    <TableHead key={yr}
                                                        className="text-right px-4 py-2 font-semibold text-muted-foreground">Year {yr} /
                                                        Lot</TableHead>
                                                ))}
                                                <TableHead className="text-right px-4 py-2 font-semibold text-muted-foreground">Target
                                                    / Lot
                                                </TableHead>
                                                <TableHead className="text-right px-4 py-2 font-semibold text-muted-foreground">Gap</TableHead>
                                            </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                            {( impact || [] ).map((grp) => {
                                                const lots = grp.lot_count || 1;
                                                const currentPerLot = ( grp.current_total || 0 ) / lots;
                                                const targetPerLot = ( grp.benefit_total || 0 ) / lots;
                                                const gapPerLot = targetPerLot - currentPerLot;
                                                // Annual move per lot = min(|current| * pct%, $amt), preserving sign
                                                const annualMoveAbs = Math.min(
                                                    Math.abs(currentPerLot) * maxChangePct / 100,
                                                    maxChangeAmt
                                                );
                                                const annualMove = gapPerLot >= 0 ? annualMoveAbs : -annualMoveAbs;
                                                return (
                                                    <TableRow key={grp.group} className="border-b hover:bg-muted">
                                                        <TableCell className="px-4 py-3 font-semibold">{grp.group}s</TableCell>
                                                        <TableCell className="px-4 py-3 text-right text-muted-foreground">{lots}</TableCell>
                                                        <TableCell className="px-4 py-3 text-right font-medium">{aud(grp.current_total)}</TableCell>
                                                        <TableCell className="px-4 py-3 text-right font-medium">{aud(currentPerLot)}</TableCell>
                                                        {[1, 2, 3, 4].map(yr => {
                                                            const yrLot = gapPerLot === 0 ? currentPerLot
                                                                : Math.abs(gapPerLot) <= Math.abs(annualMove * yr)
                                                                    ? targetPerLot
                                                                    : currentPerLot + annualMove * yr;
                                                            const delta = yrLot - currentPerLot;
                                                            return (
                                                                <TableCell key={yr} className="px-4 py-3 text-right">
                                                                    <span className="font-medium">{aud(yrLot)}</span>
                                                                    {delta !== 0 && (
                                                                        <span
                                                                            className={`ml-1 text-xs ${delta > 0 ? "text-rose-500" : "text-emerald-600"}`}>
                                        ({delta > 0 ? "+" : ""}{aud(delta)})
                                      </span>
                                                                    )}
                                                                </TableCell>
                                                            );
                                                        })}
                                                        <TableCell className={`px-4 py-3 text-right font-bold ${targetPerLot > currentPerLot ? "text-rose-600" : targetPerLot < currentPerLot ? "text-emerald-600" : "text-muted-foreground"}`}>
                                                            {aud(targetPerLot)}
                                                        </TableCell>
                                                        <TableCell className={`px-4 py-3 text-right font-bold ${gapPerLot > 0 ? "text-rose-600" : gapPerLot < 0 ? "text-emerald-600" : "text-muted-foreground"}`}>
                                                            {gapPerLot > 0 ? "+" : ""}{aud(gapPerLot)}
                                                        </TableCell>
                                                    </TableRow>
                                                );
                                            })}
                                            </TableBody>
                                        </Table>
                                    </div>
                                    <p className="text-xs text-muted-foreground italic">
                                        Year-by-year amounts shown per lot. The lower of {maxChangePct}%
                                        / {aud(maxChangeAmt)} annual caps applies.
                                        Total scheme levy remains unchanged
                                        at <strong>{aud(data?.total_budget)}</strong> in all years.
                                    </p>
                                </div>
                            )}

                            {/* Transition timeline explainer */}
                            <Card className="border-primary/20 bg-primary/10">
                                <CardContent className="pt-4 text-xs text-primary space-y-2">
                                    <p className="font-semibold">Transition Timeline (at {maxChangePct}% per-lot cap
                                        + {aud(maxChangeAmt)} $ cap)</p>
                                    {[1, 2, 3, 4].map((yr) => {
                                        const approachPct = Math.min(100, yr * maxChangePct);
                                        const schemeGap = ( impact || [] ).reduce((s, g) => s + Math.abs(( g.benefit_total || 0 ) - ( g.current_total || 0 )), 0);
                                        const movedPct = Math.min(100, Math.round(approachPct));
                                        return (
                                            <p key={yr}>
                                                <strong>Year {yr}:</strong> ~{movedPct}% of the way to the benefit-based
                                                model
                                                {schemeGap > 0 && ` (total fairness gap closed: ${aud(schemeGap * movedPct / 100)} of ${aud(schemeGap)})`}.
                                                {approachPct >= 100 ? " ✓ Fully transitioned." : ""}
                                            </p>
                                        );
                                    })}
                                    <p className="text-primary mt-1">
                                        Recommendation: Pass the Exclusive Use By-Law now, then transition levies
                                        over{" "}
                                        <strong>{maxChangePct > 0 ? Math.ceil(100 / maxChangePct) : "?"} year{Math.ceil(100 / maxChangePct) !== 1 ? "s" : ""}</strong>{" "}
                                        with a {maxChangePct}% annual cap to minimise owner disruption.
                                    </p>
                                </CardContent>
                            </Card>
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── LEVY HISTORY (Demo tab) ── */}
                <TabsContent value="demo" className="space-y-6 mt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <BarChart3 className="w-5 h-5 text-primary"/> {selectedBuilding?.name || 'Building'} Levy History
                            </CardTitle>
                            <CardDescription>
                                How the total scheme levy has changed over time. Note the 2026 reduction from 2025
                                following
                                budget optimisation. The fairness engine works on the current year's actual levy total.
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="h-72">
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={data?.levy_history || []}
                                              margin={{top: 20, right: 30, left: 20, bottom: 5}}>
                                        <CartesianGrid strokeDasharray="3 3" vertical={false}/>
                                        <XAxis dataKey="year"/>
                                        <YAxis tickFormatter={(v) => `$${( v / 1000 ).toFixed(0)}k`}/>
                                        <RTooltip formatter={(v) => aud(v)} labelFormatter={(l) => `FY${l}`}/>
                                        <Bar dataKey="total" name="Total Levy" fill="#2F4F4F" radius={[4, 4, 0, 0]}>
                                            {( data?.levy_history || [] ).map((entry, i) => (
                                                <Cell key={i}
                                                      fill={i === ( data?.levy_history || [] ).length - 1 ? "#E07A5F" : "#2F4F4F"}/>
                                            ))}
                                        </Bar>
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                            <p className="text-xs text-center text-muted-foreground mt-2">
                                Most recent year highlighted in orange. Total levy: {aud(data?.total_budget || 0)} — all
                                lots, current UOE model.
                            </p>
                        </CardContent>
                    </Card>

                    {/* Year-on-year change table */}
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-sm">Annual Levy Change Summary</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Year</TableHead>
                                        <TableHead className="text-right">Total Levy</TableHead>
                                        <TableHead className="text-right">YoY Change</TableHead>
                                        <TableHead className="text-right">YoY %</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {( data?.levy_history || [] ).map((row, i) => {
                                        const levyHistory = data?.levy_history || [];
                                        const prev = i > 0 ? levyHistory[ i - 1 ].total : null;
                                        const change = prev != null ? row.total - prev : null;
                                        const changePct = prev != null ? ( ( row.total - prev ) / prev * 100 ) : null;
                                        return (
                                            <TableRow key={row.year}
                                                      className={i === ( data?.levy_history || [] ).length - 1 ? "bg-orange-50" : ""}>
                                                {/* Plain levy year, not "FY" — the column header already
                                                    says "Year", and this is the building's own levy year
                                                    (calendar-year by default), not the Jul-Jun Australian
                                                    financial year. */}
                                                <TableCell className="font-semibold">{row.year}</TableCell>
                                                <TableCell
                                                    className="text-right font-medium">{aud(row.total)}</TableCell>
                                                <TableCell
                                                    className={`text-right font-medium ${change == null ? "" : change < 0 ? "text-emerald-600" : "text-rose-600"}`}>
                                                    {change == null ? "—" : `${change < 0 ? "−" : "+"}${aud(Math.abs(change)).replace("$", "")}`}
                                                </TableCell>
                                                <TableCell
                                                    className={`text-right font-medium ${changePct == null ? "" : changePct < 0 ? "text-emerald-600" : "text-rose-600"}`}>
                                                    {changePct == null ? "—" : pct(changePct)}
                                                </TableCell>
                                            </TableRow>
                                        );
                                    })}
                                </TableBody>
                            </Table>
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── AGM REPORT ── */}
                <TabsContent value="agm" className="space-y-6 mt-4">
                    <Card className="border-primary/20 bg-muted">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <FileText className="w-5 h-5 text-primary"/> AGM Levy Equity Analysis Report
                            </CardTitle>
                            <CardDescription>
                                Professional documents suitable for distribution at the Annual General Meeting or owner
                                information sessions.
                                Contains LBFI charts, subsidy maps, per-unit impact tables, and legal pathway
                                recommendations.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {/* Key stats preview */}
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                                <div className="rounded-lg border p-3 text-center bg-card">
                                    <div
                                        className={`text-2xl font-semibold ${lbfiBand(lbfi?.current_score ?? 0).color}`}>{lbfi?.current_score ?? "—"}</div>
                                    <div className="text-xs text-muted-foreground">LBFI Score</div>
                                </div>
                                <div className="rounded-lg border p-3 text-center bg-card">
                                    <div className="text-2xl font-semibold text-foreground">{facilities.length}</div>
                                    <div className="text-xs text-muted-foreground">Facilities Modelled</div>
                                </div>
                                <div className="rounded-lg border p-3 text-center bg-card">
                                    <div
                                        className="text-2xl font-semibold text-emerald-700">{unitImpact.filter(u => u.change < -0.5).length}</div>
                                    <div className="text-xs text-muted-foreground">Lots with Lower Levy</div>
                                </div>
                                <div className="rounded-lg border p-3 text-center bg-card">
                                    <div
                                        className="text-2xl font-semibold text-rose-700">{unitImpact.filter(u => u.change > 0.5).length}</div>
                                    <div className="text-xs text-muted-foreground">Lots with Higher Levy</div>
                                </div>
                            </div>

                            {/* PDF contents */}
                            <div className="rounded-xl bg-muted border p-4">
                                <p className="text-sm font-semibold mb-3">PDF Contents (A4 Portrait)</p>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs text-muted-foreground">
                                    {[
                                        "Executive summary and purpose statement",
                                        "LBFI gauge chart with plain-English explanation",
                                        "Levy comparison bar chart (by group)",
                                        "Cost-by-tier pie chart (Global / Apt / TH)",
                                        "Group impact table (current vs benefit-based)",
                                        "Three-tier budget breakdown (all facilities)",
                                        "Per-unit impact table (all 87 lots)",
                                        "Legal pathway options (By-Law / BMC / UOE)",
                                        "Recommended next steps for AGM",
                                        "Disclaimer and generation date",
                                    ].map((item, i) => (
                                        <div key={i} className="flex items-start gap-1.5">
                                            <CheckCircle className="w-3.5 h-3.5 text-emerald-500 flex-shrink-0 mt-0.5"/>
                                            {item}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    {/* Download cards */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                        <Card className="hover:border-primary cursor-pointer transition-all"
                              onClick={handleDownloadPdf}>
                            <CardHeader className="text-center">
                                <div className="mx-auto p-4 bg-primary/10 rounded-full w-fit mb-4">
                                    <FileText className="w-12 h-12 text-primary"/>
                                </div>
                                <CardTitle>Committee Report (PDF)</CardTitle>
                                <CardDescription>Comprehensive fairness audit, subsidy maps, and transition plans for
                                    the EC and owners.</CardDescription>
                            </CardHeader>
                        </Card>

                        <Card className="hover:border-primary cursor-pointer transition-all"
                              onClick={handleDownloadPptx}>
                            <CardHeader className="text-center">
                                <div className="mx-auto p-4 bg-orange-500/10 rounded-full w-fit mb-4">
                                    <PieIcon className="w-12 h-12 text-orange-600"/>
                                </div>
                                <CardTitle>AGM Presentation (PPTX)</CardTitle>
                                <CardDescription>Slide deck ready for presenting the fairness case to all owners at the
                                    General Meeting.</CardDescription>
                            </CardHeader>
                        </Card>

                        <Card className="hover:border-primary cursor-pointer transition-all"
                              onClick={handleDownloadCsv}>
                            <CardHeader className="text-center">
                                <div className="mx-auto p-4 bg-green-500/10 rounded-full w-fit mb-4">
                                    <Download className="w-12 h-12 text-green-600"/>
                                </div>
                                <CardTitle>Raw Data (CSV)</CardTitle>
                                <CardDescription>Export per-unit impact data for all 87 lots. Import into Excel for
                                    further analysis.</CardDescription>
                            </CardHeader>
                        </Card>
                    </div>

                    {/* AGM guidance */}
                    <Card className="border-primary/20 bg-primary/10">
                        <CardContent className="pt-4 text-xs text-primary space-y-1">
                            <p className="font-semibold">Presentation Guidance for AGM</p>
                            <p>1. Frame changes as <strong>"user-pays fairness"</strong> not a "discount for
                                townhouses."</p>
                            <p>2. Emphasise that the <strong>total levy collected stays the same</strong> — only
                                redistribution changes.</p>
                            <p>3. Run an informal information session <strong>before</strong> the AGM to build
                                consensus.</p>
                            <p>4. Engage a strata lawyer to draft the by-law before the formal vote.</p>
                            <p>5. A <strong>Special Resolution</strong> requires 75% of total vote value — prepare a
                                proxy campaign if needed.</p>
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── DISTRIBUTION ── */}
                <TabsContent value="distribution" className="space-y-4 mt-4">
                    <Suspense fallback={<div className="py-8 text-center text-muted-foreground">Loading…</div>}>
                        <LevyDistributionHistogram distribution={data?.levy_distribution}/>
                    </Suspense>
                </TabsContent>

                {/* ── SNAPSHOTS ── */}
                {canEdit && (
                    <TabsContent value="snapshots" className="space-y-4 mt-4">
                        <Suspense fallback={<div className="py-8 text-center text-muted-foreground">Loading…</div>}>
                            <ScenarioSnapshotPanel canEdit={canEdit} canDelete={canDelete} onRestored={load}/>
                        </Suspense>
                    </TabsContent>
                )}

                {/* ── AUDIT ── */}
                {canEdit && (
                    <TabsContent value="audit" className="space-y-4 mt-4">
                        <Suspense fallback={<div className="py-8 text-center text-muted-foreground">Loading…</div>}>
                            <FairnessAuditLog/>
                        </Suspense>
                    </TabsContent>
                )}

                {/* ── CROSS-SUBSIDY ── */}
                <TabsContent value="cross-subsidy" className="space-y-4 mt-4">
                    <Suspense fallback={<div className="py-8 text-center text-muted-foreground">Loading…</div>}>
                        <CrossSubsidyTable crossSubsidy={data?.cross_subsidy_report}/>
                    </Suspense>
                </TabsContent>

            </Tabs>

            {/* ── UNIT EXPLAIN DRAWER ── */}
            <Suspense fallback={null}>
                <UnitExplainDrawer unit={explainUnit} open={explainOpen} onClose={() => setExplainOpen(false)}/>
            </Suspense>

        </div>
    );
};

export default LevyFairnessPage;
