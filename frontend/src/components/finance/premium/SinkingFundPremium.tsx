"use client";
import React, {useState} from "react";
import {
    Bar,
    CartesianGrid,
    ComposedChart,
    Legend,
    Line,
    ReferenceLine,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Button} from "@/components/ui/button";
import {AlertTriangle, Building2, CalendarClock, Info, TrendingDown, TrendingUp} from "lucide-react";
import InfoButton from "./InfoButton";

// ─── Types ──────────────────────────────────────────────────────────────────

interface PlanRow {
    year: number;
    contribution: number;
    expenditure: number;
    opening_balance: number;
    closing_balance: number;
    categories: Record<string, number>;
    category_labels: Record<string, string>;
    is_actual: boolean;
    is_major_capital_year: boolean;
}

interface CapitalEvent {
    year: number;
    label: string;
    description: string;
    amount: number;
    severity: "critical" | "high";
}

interface SinkingFundData {
    plan: PlanRow[];
    capital_events: CapitalEvent[];
    category_totals: Record<string, number>;
    category_labels: Record<string, string>;
    summary: {
        total_contributions: number;
        total_expenditures: number;
        closing_balance_2035: number;
        major_capital_years: number[];
        years_remaining_to_2035: number;
    };
    seeded: boolean;
}

interface ProjectionRow {
    year: number;
    base_expenditure: number;
    inflated_expenditure: number;
    annual_collection: number;
    projected_reserve: number;
    is_actual: boolean;
    is_major_capital_year: boolean;
}

interface ProjectionData {
    rows: ProjectionRow[];
    annual_collection: number;
    inflation_rate: number;
    total_inflated_expenditure: number;
    seeded: boolean;
}

interface CapitalShockIndexRow {
    year: number;
    capital_spend: number;
    baseline_spend: number;
    capital_shock_index: number;
    risk_level: string;
    shock_category: string;
}

interface CapitalShockInsights {
    capital_shock_index?: {
        baseline_spend: number;
        next_shock_year?: number | null;
        next_shock?: CapitalShockIndexRow | null;
        rows: CapitalShockIndexRow[];
    };
    reserve_collapse?: {
        start_year: number;
        initial_reserve: number;
        collapse_year?: number | null;
        years_to_collapse?: number | null;
        risk_level: string;
        risk_label: string;
        minimum_balance: number;
        minimum_balance_year?: number | null;
    };
    funding_adequacy?: {
        sfar?: number | null;
        status: string;
        status_label: string;
        funding_gap: number;
        available_funding: number;
        capital_required: number;
    };
}

interface SinkingFundPremiumProps {
    data: SinkingFundData | null;
    projectionData?: ProjectionData | null;
    insights?: CapitalShockInsights | null;
    loading?: boolean;
    canEdit?: boolean;
    onSavePlan?: (plan: PlanRow[]) => Promise<void> | void;
    onSaveEvents?: (events: CapitalEvent[]) => Promise<void> | void;
}
// ─── Formatters ──────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: fmt
 * Path: frontend/src/components/finance/premium/SinkingFundPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmt = (v: number) =>
    `$${Math.round(v).toLocaleString("en-AU")}`;
/**
 * @generated FunctionHeader
 * Function: fmtK
 * Path: frontend/src/components/finance/premium/SinkingFundPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmtK = (v: number) => {
    if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
    if (Math.abs(v) >= 1_000) return `$${Math.round(v / 1_000)}K`;
    return `$${Math.round(v)}`;
};
// ─── Custom Tooltip ──────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: CustomTooltip
 * Path: frontend/src/components/finance/premium/SinkingFundPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CustomTooltip = ({active, payload, label}: any) => {
    if (!active || !payload || !payload.length) return null;
    const row = payload[0]?.payload as PlanRow;
    return (
        <div
            className="bg-popover border border-border rounded-xl p-4 shadow-md text-xs min-w-[220px]">
            <p className="font-semibold text-foreground text-sm mb-3">
                FY {label}
                {row?.is_actual && (
                    <span
                        className="ml-2 text-[9px] bg-emerald-100 text-emerald-700 px-1.5 py-0.5 rounded-full font-semibold uppercase">Actual</span>
                )}
                {!row?.is_actual && (
                    <span
                        className="ml-2 text-[9px] bg-primary/10 text-primary px-1.5 py-0.5 rounded-full font-semibold uppercase">Projected</span>
                )}
            </p>
            {payload.map((entry: any) => (
                <div key={entry.dataKey} className="flex justify-between gap-6 py-0.5">
                    <span style={{color: entry.color}} className="font-bold">{entry.name}</span>
                    <span className="font-semibold text-foreground">{fmt(entry.value)}</span>
                </div>
            ))}
            {row?.is_major_capital_year && (
                <div className="mt-3 pt-3 border-t border-rose-100">
                    <p className="text-rose-600 font-semibold text-[10px] uppercase tracking-wide flex items-center gap-1">
                        <AlertTriangle size={10}/> Major Capital Year
                    </p>
                </div>
            )}
        </div>
    );
};
// ─── Category breakdown table ─────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: CategoryTable
 * Path: frontend/src/components/finance/premium/SinkingFundPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CategoryTable = ({
                           category_totals,
                           category_labels,
                       }: {
    category_totals: Record<string, number>;
    category_labels: Record<string, string>;
}) => {
    const sorted = Object.entries(category_totals)
        .sort(([, a], [, b]) => b - a)
        .slice(0, 10); // top 10

    const max = sorted[0]?.[1] ?? 1;

    return (
        <div className="space-y-3">
            {sorted.map(([key, total]) => (
                <div key={key} className="group">
                    <div className="flex justify-between items-center mb-1">
            <span className="text-xs font-semibold text-muted-foreground truncate pr-4 max-w-[200px]">
              {category_labels[key] ?? key}
            </span>
                        <span className="text-xs font-semibold text-foreground tabular-nums">{fmt(total)}</span>
                    </div>
                    <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                        <div
                            className="h-full rounded-full bg-primary transition-all duration-500"
                            style={{width: `${(total / max) * 100}%`}}
                        />
                    </div>
                </div>
            ))}
        </div>
    );
};
// ─── Capital Event Card ───────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: CapitalEventCard
 * Path: frontend/src/components/finance/premium/SinkingFundPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CapitalEventCard = ({event, currentYear = 2026}: { event: CapitalEvent; currentYear?: number }) => {
    const yearsAway = event.year - currentYear;
    const isPast = yearsAway <= 0;
    const isImminent = yearsAway > 0 && yearsAway <= 3;

    return (
        <div
            className={`rounded-2xl p-4 border transition-all ${
                isPast
                    ? "bg-muted border-border opacity-60"
                    : event.severity === "critical"
                        ? "bg-rose-50 border-rose-200"
                        : "bg-amber-50 border-amber-200"
            }`}
        >
            <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
            <span
                className={`text-[9px] font-semibold uppercase tracking-widest px-2 py-0.5 rounded-full ${
                    isPast
                        ? "bg-muted text-muted-foreground"
                        : event.severity === "critical"
                            ? "bg-rose-200 text-rose-700"
                            : "bg-amber-200 text-amber-700"
                }`}
            >
              {isPast ? "Completed" : isImminent ? "Imminent" : `${yearsAway}y away`}
            </span>
                        <span className="text-[10px] font-bold text-muted-foreground">FY {event.year}</span>
                    </div>
                    <p className="text-sm font-semibold text-foreground">{event.label}</p>
                    <p className="text-xs text-muted-foreground font-medium mt-0.5 leading-relaxed">{event.description}</p>
                </div>
                <div className="text-right shrink-0">
                    <p className="text-lg font-semibold text-foreground">{fmtK(event.amount)}</p>
                    <p className="text-[9px] text-muted-foreground font-medium uppercase">Budget</p>
                </div>
            </div>
        </div>
    );
};
// ─── Main Component ───────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: SinkingFundPremium
 * Path: frontend/src/components/finance/premium/SinkingFundPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const SinkingFundPremium: React.FC<SinkingFundPremiumProps> = ({
                                                                   data,
                                                                   projectionData,
                                                                   insights,
                                                                   loading,
                                                                   canEdit,
                                                                   onSavePlan,
                                                                   onSaveEvents,
                                                               }) => {
    const [showCategories, setShowCategories] = useState(false);
    const [editing, setEditing] = useState(false);
    const [planDraft, setPlanDraft] = useState<PlanRow[]>([]);
    const [eventsDraft, setEventsDraft] = useState<CapitalEvent[]>([]);
    const [saving, setSaving] = useState(false);

    if (loading) {
        return (
            <div className="animate-pulse space-y-4">
                <div className="h-80 rounded-xl bg-muted"/>
                <div className="grid grid-cols-3 gap-4">
                    {[1, 2, 3].map(i => <div key={i} className="h-24 rounded-2xl bg-muted"/>)}
                </div>
            </div>
        );
    }

    if (!data || !data.seeded || !data.plan.length) {
        return (
            <Card className="rounded-xl border border-dashed border-border bg-card/40">
                <CardContent className="p-12 flex flex-col items-center justify-center text-center gap-4">
                    <Building2 className="w-12 h-12 text-muted-foreground"/>
                    <div>
                        <p className="font-semibold text-muted-foreground">Capital Works Plan Not Loaded</p>
                        <p className="text-xs text-muted-foreground mt-1">
                            Run <code
                            className="bg-muted px-1 rounded">scripts/finances/seed_sinking_fund_plan.py</code> to
                            import the 15-year plan.
                        </p>
                    </div>
                </CardContent>
            </Card>
        );
    }

    const {plan, capital_events, category_totals, category_labels, summary} = data;
    const shockIndex = insights?.capital_shock_index;
    const collapse = insights?.reserve_collapse;
    const adequacy = insights?.funding_adequacy;
    /**
     * @generated FunctionHeader
     * Function: startEditing
     * Path: frontend/src/components/finance/premium/SinkingFundPremium.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const startEditing = () => {
        if (!data) return;
        setPlanDraft(plan.map(row => ({...row})));
        setEventsDraft(capital_events.map(ev => ({...ev})));
        setEditing(true);
    };
    /**
     * @generated FunctionHeader
     * Function: cancelEditing
     * Path: frontend/src/components/finance/premium/SinkingFundPremium.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const cancelEditing = () => {
        setEditing(false);
        setPlanDraft([]);
        setEventsDraft([]);
    };
    /**
     * @generated FunctionHeader
     * Function: updatePlanField
     * Path: frontend/src/components/finance/premium/SinkingFundPremium.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updatePlanField = (year: number, field: keyof PlanRow, value: any) => {
        setPlanDraft((prev) => prev.map(row => {
            if (row.year !== year) return row;
            return {...row, [field]: value};
        }));
    };
    /**
     * @generated FunctionHeader
     * Function: updateEventField
     * Path: frontend/src/components/finance/premium/SinkingFundPremium.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateEventField = (index: number, field: keyof CapitalEvent, value: any) => {
        setEventsDraft((prev) => prev.map((ev, idx) => {
            if (idx !== index) return ev;
            return {...ev, [field]: value};
        }));
    };
    /**
     * @generated FunctionHeader
     * Function: handleSaveEdits
     * Path: frontend/src/components/finance/premium/SinkingFundPremium.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSaveEdits = async () => {
        if (!onSavePlan && !onSaveEvents) return;
        setSaving(true);
        try {
            if (onSavePlan) {
                await onSavePlan(planDraft);
            }
            if (onSaveEvents) {
                await onSaveEvents(eventsDraft);
            }
            setEditing(false);
        } finally {
            setSaving(false);
        }
    };

    // Current year actuals vs projections boundary.
    //
    // Read from the clock, not hardcoded. This was `= 2026`, so from 1 January 2027
    // the "NOW" divider would have sat on 2026 and silently relabelled a year of
    // projections as actuals — the chart's actual-vs-forecast boundary is the one
    // thing on it that must not be a guess.
    const CURRENT_YEAR = new Date().getFullYear();

    // Build a lookup map for projection data by year
    const projMap = new Map<number, ProjectionRow>();
    projectionData?.rows?.forEach(r => projMap.set(r.year, r));
    const annualCollectionModel = projectionData?.annual_collection ?? 0;
    // NO FALLBACK RATE. This was `?? 0.035`, and the caption below states the value
    // as fact ("3.5% CPI model, smoothed 14yr"). When the projection omitted a rate
    // the UI therefore asserted a CPI assumption the building had never configured
    // — a component constructing financial truth, which the finance architecture
    // forbids outright. `null` means "not supplied" and renders as such.
    const inflationRate: number | null = projectionData?.inflation_rate ?? null;

    // Prepare chart data — merge plan + projection
    const chartData = plan.map(row => {
        const proj = projMap.get(row.year);
        return {
            ...row,
            year: String(row.year),
            balance_k: Math.round(row.closing_balance / 1_000),
            contrib_k: Math.round(row.contribution / 1_000),
            spend_k: Math.round(row.expenditure / 1_000),
            inflated_k: proj ? Math.round(proj.inflated_expenditure / 1_000) : null,
            model_reserve_k: proj ? Math.round(proj.projected_reserve / 1_000) : null,
            collection_k: proj ? Math.round(annualCollectionModel / 1_000) : null,
        };
    });

    const closing2035 = summary?.closing_balance_2035 ?? 0;
    const totalExpend = summary?.total_expenditures ?? 0;
    const totalContrib = summary?.total_contributions ?? 0;
    /**
     * @generated FunctionHeader
     * Function: shockBadge
     * Path: frontend/src/components/finance/premium/SinkingFundPremium.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const shockBadge = (level?: string) => {
        switch ((level || "").toLowerCase()) {
            case "severe":
                return "bg-rose-100 text-rose-700 border-rose-200";
            case "major":
                return "bg-orange-100 text-orange-700 border-orange-200";
            case "moderate":
                return "bg-amber-100 text-amber-700 border-amber-200";
            default:
                return "bg-emerald-100 text-emerald-700 border-emerald-200";
        }
    };

    return (
        <div className="space-y-8">

            {/* ── Super Admin Plan Editor ── */}
            {canEdit && (
                <Card className="rounded-xl border-border bg-card">
                    <CardHeader className="pb-4">
                        <CardTitle className="text-lg font-semibold text-foreground">Sinking Fund Plan Editor</CardTitle>
                        <CardDescription className="text-muted-foreground font-medium">
                            Update annual contributions, expenditures, and major capital events.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-6">
                        {!editing ? (
                            <div className="flex items-center justify-between">
                                <div className="text-sm text-muted-foreground">
                                    Changes will recalculate opening/closing balances across the plan.
                                </div>
                                <Button className="rounded-xl" onClick={startEditing}>
                                    Edit Plan Data
                                </Button>
                            </div>
                        ) : (
                            <div className="space-y-6">
                                <div className="overflow-x-auto border border-border rounded-2xl">
                                    <table className="min-w-full text-xs">
                                        <thead
                                            className="bg-muted text-muted-foreground uppercase tracking-widest text-[10px]">
                                        <tr>
                                            <th className="px-3 py-2 text-left">Year</th>
                                            <th className="px-3 py-2 text-right">Contribution</th>
                                            <th className="px-3 py-2 text-right">Expenditure</th>
                                            <th className="px-3 py-2 text-center">Actual</th>
                                            <th className="px-3 py-2 text-center">Major</th>
                                        </tr>
                                        </thead>
                                        <tbody>
                                        {planDraft.map((row) => (
                                            <tr key={row.year} className="border-t border-border">
                                                <td className="px-3 py-2 font-bold text-foreground">FY {row.year}</td>
                                                <td className="px-3 py-2 text-right">
                                                    <input
                                                        type="number"
                                                        className="w-28 rounded-lg border border-border px-2 py-1 text-right text-xs"
                                                        value={row.contribution}
                                                        min="0"
                                                        onChange={(e) => updatePlanField(row.year, "contribution", Number(e.target.value))}
                                                    />
                                                </td>
                                                <td className="px-3 py-2 text-right">
                                                    <input
                                                        type="number"
                                                        className="w-28 rounded-lg border border-border px-2 py-1 text-right text-xs"
                                                        value={row.expenditure}
                                                        min="0"
                                                        onChange={(e) => updatePlanField(row.year, "expenditure", Number(e.target.value))}
                                                    />
                                                </td>
                                                <td className="px-3 py-2 text-center">
                                                    <input
                                                        type="checkbox"
                                                        checked={!!row.is_actual}
                                                        onChange={(e) => updatePlanField(row.year, "is_actual", e.target.checked)}
                                                    />
                                                </td>
                                                <td className="px-3 py-2 text-center">
                                                    <input
                                                        type="checkbox"
                                                        checked={!!row.is_major_capital_year}
                                                        onChange={(e) => updatePlanField(row.year, "is_major_capital_year", e.target.checked)}
                                                    />
                                                </td>
                                            </tr>
                                        ))}
                                        </tbody>
                                    </table>
                                </div>

                                <div className="space-y-3">
                                    <div className="flex items-center justify-between">
                                        <div className="text-sm font-bold text-foreground">Capital Events</div>
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="rounded-xl"
                                            onClick={() => setEventsDraft((prev) => ([
                                                ...prev,
                                                {
                                                    year: new Date().getFullYear(),
                                                    label: "",
                                                    description: "",
                                                    amount: 0,
                                                    severity: "high"
                                                },
                                            ]))}
                                        >
                                            Add Event
                                        </Button>
                                    </div>
                                    <div className="space-y-2">
                                        {eventsDraft.map((ev, idx) => (
                                            <div key={`${ev.year}-${idx}`}
                                                 className="grid grid-cols-1 md:grid-cols-6 gap-2">
                                                <input
                                                    type="number"
                                                    className="rounded-lg border border-border px-2 py-1 text-xs"
                                                    value={ev.year}
                                                    onChange={(e) => updateEventField(idx, "year", Number(e.target.value))}
                                                />
                                                <input
                                                    type="text"
                                                    className="rounded-lg border border-border px-2 py-1 text-xs md:col-span-2"
                                                    value={ev.label || ""}
                                                    placeholder="Event label"
                                                    onChange={(e) => updateEventField(idx, "label", e.target.value)}
                                                />
                                                <input
                                                    type="text"
                                                    className="rounded-lg border border-border px-2 py-1 text-xs md:col-span-2"
                                                    value={ev.description || ""}
                                                    placeholder="Description"
                                                    onChange={(e) => updateEventField(idx, "description", e.target.value)}
                                                />
                                                <input
                                                    type="number"
                                                    className="rounded-lg border border-border px-2 py-1 text-xs"
                                                    value={ev.amount}
                                                    onChange={(e) => updateEventField(idx, "amount", Number(e.target.value))}
                                                />
                                                <select
                                                    className="rounded-lg border border-border px-2 py-1 text-xs"
                                                    value={ev.severity}
                                                    onChange={(e) => updateEventField(idx, "severity", e.target.value)}
                                                >
                                                    <option value="critical">Critical</option>
                                                    <option value="high">High</option>
                                                    <option value="moderate">Moderate</option>
                                                </select>
                                            </div>
                                        ))}
                                    </div>
                                </div>

                                <div className="flex items-center gap-2">
                                    <Button className="rounded-xl" onClick={handleSaveEdits} disabled={saving}>
                                        {saving ? "Saving..." : "Save Changes"}
                                    </Button>
                                    <Button variant="outline" className="rounded-xl" onClick={cancelEditing}
                                            disabled={saving}>
                                        Cancel
                                    </Button>
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* ── KPI strip ── */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="rounded-2xl bg-card border border-border shadow-sm p-5">
                    <div className="flex items-center gap-1 mb-1">
                        <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Plan Balance
                            (2035)</p>
                        <InfoButton
                            title="Plan Balance (2035)"
                            description="The projected sinking fund closing balance at the end of the 15-year plan (FY 2035). This is calculated from the original approved plan using base costs without CPI inflation. The plan targets ~$0 at 2035, meaning the fund is fully self-funding over the period."
                            dataSources={["sinking_fund_plan"]}
                        />
                    </div>
                    <p className="text-2xl font-semibold text-foreground">{fmt(closing2035)}</p>
                    <p className="text-xs text-emerald-600 font-bold mt-1 flex items-center gap-1">
                        <TrendingUp size={12}/> Original plan (no inflation)
                    </p>
                </div>
                <div className="rounded-2xl bg-card border border-border shadow-sm p-5">
                    <div className="flex items-center gap-1 mb-1">
                        <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Required Annual
                            Levy</p>
                        <InfoButton
                            title="Required Annual Levy"
                            description="The recommended annual sinking fund collection needed to fully fund all 15-year capital works after applying CPI inflation. This is smoothed into a flat amount across the plan period. If your current levy is below this, the fund may be under-collected when major works arrive."
                            dataSources={["sinking_fund_projection"]}
                        />
                    </div>
                    <p className="text-2xl font-semibold text-primary">
                        {annualCollectionModel > 0 ? fmt(annualCollectionModel) : "—"}
                    </p>
                    <p className="text-xs text-muted-foreground font-medium mt-1">
                        {inflationRate !== null
                            ? `${(inflationRate * 100).toFixed(1)}% CPI model, smoothed 14yr`
                            : "CPI assumption not supplied by the projection"}
                    </p>
                </div>
                <div className="rounded-2xl bg-card border border-border shadow-sm p-5">
                    <div className="flex items-center gap-1 mb-1">
                        <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Total Works
                            (15yr)</p>
                        <InfoButton
                            title="Total Capital Works (15-Year)"
                            description="The total planned capital expenditure over the full 15-year period at base (uninflated) cost. The inflation-adjusted figure in brackets shows the real cost after applying 3.5% CPI compounding — this is what the fund actually needs to cover in today's dollars."
                            dataSources={["sinking_fund_plan", "sinking_fund_projection"]}
                        />
                    </div>
                    <p className="text-2xl font-semibold text-rose-600">{fmt(totalExpend)}</p>
                    <p className="text-xs text-muted-foreground font-medium mt-1">Base cost
                        (inflation-adjusted: {projectionData ? fmt(projectionData.total_inflated_expenditure) : "—"})</p>
                </div>
                <div className="rounded-2xl bg-card border border-border shadow-sm p-5">
                    <div className="flex items-center gap-1 mb-1">
                        <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Critical
                            Events</p>
                        <InfoButton
                            title="Critical Capital Events"
                            description="The number of major capital works events planned over the 15-year period. These are years with large, concentrated expenditure (e.g. lift replacements, external painting, intercom upgrades) that require significant fund reserves to be available. See the Capital Events Timeline below for details."
                            dataSources={["sinking_fund_capital_events"]}
                        />
                    </div>
                    <p className="text-2xl font-semibold text-amber-600">{capital_events.length}</p>
                    <p className="text-xs text-muted-foreground font-medium mt-1">Major capital works planned</p>
                </div>
            </div>

            {/* ── Capital Shock & Funding Adequacy ── */}
            {(shockIndex || collapse || adequacy) && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div className="rounded-2xl bg-card border border-border shadow-sm p-5">
                        <div
                            className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">
                            Capital Shock Index
                        </div>
                        <p className="text-2xl font-semibold text-foreground">
                            {shockIndex?.baseline_spend ? fmt(shockIndex.baseline_spend) : "—"}
                        </p>
                        {shockIndex?.next_shock ? (
                            <p className="text-xs text-muted-foreground mt-1">
                                Next spike FY {shockIndex.next_shock.year} •
                                <span
                                    className={`ml-2 inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-semibold ${shockBadge(shockIndex.next_shock.risk_level)}`}>
                  {shockIndex.next_shock.risk_level}
                </span>
                            </p>
                        ) : (
                            <p className="text-xs text-muted-foreground mt-1">No major spikes detected</p>
                        )}
                    </div>
                    <div className="rounded-2xl bg-card border border-border shadow-sm p-5">
                        <div
                            className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">
                            Reserve Stability
                        </div>
                        <p className="text-2xl font-semibold text-foreground">
                            {collapse?.risk_label || "—"}
                        </p>
                        <p className="text-xs text-muted-foreground mt-1">
                            Minimum balance {collapse ? fmt(collapse.minimum_balance) : "—"}
                        </p>
                    </div>
                    <div className="rounded-2xl bg-card border border-border shadow-sm p-5">
                        <div
                            className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">
                            Funding Adequacy
                        </div>
                        <p className="text-2xl font-semibold text-foreground">
                            {adequacy?.sfar !== undefined && adequacy?.sfar !== null ? adequacy.sfar.toFixed(2) : "—"}
                        </p>
                        <p className="text-xs text-muted-foreground mt-1">
                            {adequacy?.status_label || "No funding status"}
                        </p>
                    </div>
                </div>
            )}

            {/* ── Main chart ── */}
            <Card className="rounded-xl border-border bg-card shadow-xl overflow-hidden">
                <CardHeader className="pb-0 px-8 pt-8">
                    <div className="flex items-start justify-between gap-4">
                        <div>
                            <CardTitle className="text-xl font-semibold text-foreground flex items-center gap-2">
                                <Building2 className="w-5 h-5 text-primary"/>
                                15-Year Capital Works Forecast
                                <InfoButton
                                    title="15-Year Capital Works Forecast"
                                    description="Shows the sinking fund's financial trajectory from FY 2021 to FY 2035, combining annual levy collections, planned capital expenditure, and projected fund reserves."
                                    dataSources={["sinking_fund_plan", "sinking_fund_capital_events", "sinking_fund_projection"]}
                                    logic={
                                        <div className="space-y-2">
                                            <p><span className="font-bold text-foreground">Emerald bars (Levy Contribution):</span> The
                                                annual amount collected into the sinking fund each year.</p>
                                            <p><span
                                                className="font-bold text-foreground">Red bars (Base Spend):</span> The
                                                planned capital expenditure for that year at today's base cost — no
                                                inflation applied.</p>
                                            <p><span className="font-bold text-amber-600">Amber dashed line (Inflated Spend):</span> The
                                                same capital expenditure adjusted for 3.5% CPI inflation compounding
                                                each year — this is the <em>real</em> future cost you need to plan for.
                                            </p>
                                            <p><span className="font-bold text-primary">Indigo dashed line (Plan Balance):</span> The
                                                cumulative sinking fund reserve at year-end per the original approved
                                                plan, using base costs.</p>
                                            <p><span className="font-bold text-primary">Purple solid line (Model Reserve):</span> The
                                                inflation-corrected fund balance — what the reserve will actually look
                                                like if costs rise at 3.5%/year. Compare this to the plan balance to see
                                                the real funding gap.</p>
                                            <p><span
                                                className="font-bold text-primary">Blue "NOW" line:</span> Separates
                                                historical actuals (left) from future projections (right).</p>
                                            <p><span
                                                className="font-bold text-rose-500">Rose/amber vertical lines:</span> Mark
                                                years with major capital events (lift replacement, repainting, intercom
                                                upgrades).</p>
                                            <p><span className="font-bold text-emerald-600">Green dashed horizontal line:</span> The
                                                recommended annual levy collection target to fully fund all works under
                                                the inflation model. If your current levy is below this line, the fund
                                                will be under-resourced when major works arrive.</p>
                                        </div>
                                    }
                                />
                            </CardTitle>
                            <CardDescription className="text-muted-foreground font-medium mt-1">
                                Base vs. CPI-adjusted expenditures, recommended collection &amp; fund reserve trajectory
                            </CardDescription>
                        </div>
                        <div
                            className="flex flex-wrap items-center gap-3 text-[10px] text-muted-foreground font-bold shrink-0">
                            <span className="flex items-center gap-1"><span
                                className="w-3 h-3 rounded-sm bg-emerald-400 inline-block"/> Base levy</span>
                            <span className="flex items-center gap-1"><span
                                className="w-3 h-3 rounded-sm bg-rose-400 inline-block"/> Base spend</span>
                            <span className="flex items-center gap-1"><span
                                className="w-2 h-2 rounded-full bg-amber-500 inline-block"/> Inflated spend</span>
                            <span className="flex items-center gap-1"><span
                                className="w-2 h-2 rounded-full bg-primary inline-block"/> Model reserve</span>
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="pt-6 pb-8 px-4">
                    <ResponsiveContainer width="100%" height={360}>
                        <ComposedChart data={chartData} margin={{top: 10, right: 24, left: 10, bottom: 10}}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9"/>
                            <XAxis
                                dataKey="year"
                                tick={{fontSize: 11, fontWeight: 700, fill: "#64748b"}}
                                tickLine={false}
                                axisLine={false}
                            />
                            <YAxis
                                yAxisId="bars"
                                tickFormatter={(v) => `$${Math.round(v)}K`}
                                tick={{fontSize: 10, fill: "#94a3b8"}}
                                tickLine={false}
                                axisLine={false}
                                width={60}
                            />
                            <YAxis
                                yAxisId="line"
                                orientation="right"
                                tickFormatter={(v) => `$${Math.round(v)}K`}
                                tick={{fontSize: 10, fill: "#94a3b8"}}
                                tickLine={false}
                                axisLine={false}
                                width={60}
                            />
                            <Tooltip content={<CustomTooltip/>}/>
                            <Legend
                                wrapperStyle={{fontSize: 11, fontWeight: 700, paddingTop: 16}}
                                formatter={(value) => <span style={{color: "#475569"}}>{value}</span>}
                            />

                            {/* Actual vs projected separator */}
                            <ReferenceLine
                                x={String(CURRENT_YEAR)}
                                yAxisId="bars"
                                stroke="#6366f1"
                                strokeDasharray="4 4"
                                strokeWidth={2}
                                label={{value: "NOW", position: "top", fontSize: 9, fontWeight: 900, fill: "#6366f1"}}
                            />

                            {/* Major capital event markers */}
                            {capital_events.map((ev) => (
                                <ReferenceLine
                                    key={ev.year}
                                    x={String(ev.year)}
                                    yAxisId="bars"
                                    stroke={ev.severity === "critical" ? "#f43f5e" : "#f59e0b"}
                                    strokeDasharray="6 3"
                                    strokeWidth={1.5}
                                />
                            ))}

                            <Bar
                                yAxisId="bars"
                                dataKey="contrib_k"
                                name="Levy Contribution ($K)"
                                fill="#34d399"
                                opacity={0.80}
                                radius={[4, 4, 0, 0]}
                                maxBarSize={16}
                            />
                            <Bar
                                yAxisId="bars"
                                dataKey="spend_k"
                                name="Base Spend ($K)"
                                fill="#f87171"
                                opacity={0.80}
                                radius={[4, 4, 0, 0]}
                                maxBarSize={16}
                            />
                            {/* Inflation-adjusted expenditure line */}
                            {projectionData && (
                                <Line
                                    yAxisId="bars"
                                    type="monotone"
                                    dataKey="inflated_k"
                                    name="Inflated Spend ($K)"
                                    stroke="#f59e0b"
                                    strokeWidth={2}
                                    strokeDasharray="5 3"
                                    dot={{r: 3, fill: "#f59e0b", strokeWidth: 0}}
                                    connectNulls
                                />
                            )}
                            {/* Original plan fund balance line */}
                            <Line
                                yAxisId="line"
                                type="monotone"
                                dataKey="balance_k"
                                name="Plan Balance ($K)"
                                stroke="#6366f1"
                                strokeWidth={2}
                                strokeDasharray="6 2"
                                dot={{r: 2.5, fill: "#6366f1", strokeWidth: 0}}
                                activeDot={{r: 5, fill: "#6366f1", stroke: "white", strokeWidth: 2}}
                            />
                            {/* Inflation-model reserve line */}
                            {projectionData && (
                                <Line
                                    yAxisId="line"
                                    type="monotone"
                                    dataKey="model_reserve_k"
                                    name="Model Reserve ($K)"
                                    stroke="#7c3aed"
                                    strokeWidth={2.5}
                                    dot={{r: 3, fill: "#7c3aed", strokeWidth: 0}}
                                    activeDot={{r: 6, fill: "#7c3aed", stroke: "white", strokeWidth: 2}}
                                    connectNulls
                                />
                            )}
                            {/* Recommended annual collection reference line */}
                            {annualCollectionModel > 0 && (
                                <ReferenceLine
                                    yAxisId="bars"
                                    y={Math.round(annualCollectionModel / 1_000)}
                                    stroke="#10b981"
                                    strokeWidth={1.5}
                                    strokeDasharray="8 4"
                                    label={{
                                        value: `$${Math.round(annualCollectionModel / 1_000)}K/yr target`,
                                        position: "insideTopRight",
                                        fontSize: 9,
                                        fontWeight: 900,
                                        fill: "#10b981",
                                    }}
                                />
                            )}
                        </ComposedChart>
                    </ResponsiveContainer>
                </CardContent>
            </Card>

            {/* ── Capital events timeline ── */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                <Card className="rounded-xl border-border bg-card shadow-xl overflow-hidden">
                    <CardHeader className="px-8 pt-8 pb-4">
                        <CardTitle className="text-lg font-semibold text-foreground flex items-center gap-2">
                            <CalendarClock className="w-5 h-5 text-rose-500"/>
                            Capital Events Timeline
                        </CardTitle>
                        <CardDescription className="text-muted-foreground font-medium">
                            Major planned capital works requiring significant funding
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="px-8 pb-8 space-y-3">
                        {capital_events.map((ev) => (
                            <CapitalEventCard key={ev.year} event={ev} currentYear={CURRENT_YEAR}/>
                        ))}
                    </CardContent>
                </Card>

                {/* ── Category breakdown ── */}
                <Card className="rounded-xl border-border bg-card shadow-xl overflow-hidden">
                    <CardHeader className="px-8 pt-8 pb-4">
                        <div className="flex items-center justify-between">
                            <div>
                                <CardTitle className="text-lg font-semibold text-foreground">
                                    Top Spend Categories
                                </CardTitle>
                                <CardDescription className="text-muted-foreground font-medium">
                                    15-year aggregate across 24 capital categories
                                </CardDescription>
                            </div>
                            <button
                                onClick={() => setShowCategories(!showCategories)}
                                className="text-[10px] font-semibold uppercase tracking-widest text-primary hover:text-primary transition-colors"
                            >
                                {showCategories ? "Show Top 10" : "All"}
                            </button>
                        </div>
                    </CardHeader>
                    <CardContent className="px-8 pb-8">
                        {Object.keys(category_totals).length > 0 ? (
                            <CategoryTable
                                category_totals={
                                    showCategories
                                        ? category_totals
                                        : Object.fromEntries(
                                            Object.entries(category_totals)
                                                .sort(([, a], [, b]) => b - a)
                                                .slice(0, 10)
                                        )
                                }
                                category_labels={category_labels}
                            />
                        ) : (
                            <p className="text-sm text-muted-foreground font-medium">No category data available.</p>
                        )}
                    </CardContent>
                </Card>
            </div>

            {/* ── Methodology note ── */}
            {/* ── Reserve adequacy alert ── */}
            {projectionData && (() => {
                const currentRow = projectionData.rows.find(r => r.year === CURRENT_YEAR);
                const planRow = data.plan.find(r => r.year === CURRENT_YEAR);
                if (!currentRow || !planRow) return null;
                const modelReserve = currentRow.projected_reserve;
                const planBalance = planRow.closing_balance;
                const adequacy = planBalance / modelReserve;
                const isUnder = adequacy < 0.90;
                const shortfall = Math.max(0, modelReserve - planBalance);
                return (
                    <div className={`flex items-start gap-3 text-xs rounded-2xl p-4 border ${
                        isUnder
                            ? "bg-rose-50 border-rose-200 text-rose-700"
                            : "bg-emerald-50 border-emerald-200 text-emerald-700"
                    }`}>
                        {isUnder ? <TrendingDown size={14} className="shrink-0 mt-0.5"/> :
                            <TrendingUp size={14} className="shrink-0 mt-0.5"/>}
                        <p>
                            <span className="font-semibold">Reserve Adequacy {CURRENT_YEAR}: </span>
                            {isUnder
                                ? `Current sinking fund plan shows ${fmt(planBalance)} vs. inflation-model target of ${fmt(modelReserve)} — a ${fmt(shortfall)} shortfall (${Math.round(adequacy * 100)}% funded). Consider increasing the annual levy toward the recommended ${fmt(projectionData.annual_collection)}/yr.`
                                : `Sinking fund is on track — ${fmt(planBalance)} vs. model target ${fmt(modelReserve)} (${Math.round(adequacy * 100)}% funded). No immediate levy increase required.`
                            }
                        </p>
                    </div>
                );
            })()}

            <div
                className="flex items-start gap-3 text-xs text-muted-foreground bg-muted rounded-2xl p-4 border border-border">
                <Info size={14} className="shrink-0 mt-0.5 text-muted-foreground"/>
                <p>
                    <span className="font-semibold text-muted-foreground">Data source:</span> 15-year Sinking Fund Capital Works
                    Plan prepared by strata management.
                    Years 2021–2026 reflect actual or approved amounts. Years 2027–2035 are projected.
                    The <span className="font-bold text-muted-foreground">inflation model</span> (dashed amber + purple lines)
                    applies 3.5% CPI compounding to each year's
                    base expenditure and smooths the total into a flat annual collection of{" "}
                    {projectionData ? <span
                        className="font-semibold text-foreground">{fmt(projectionData.annual_collection)}/yr</span> : "$106,763/yr"}.
                    Fund balance of ~$0 at 2035 is by design — the plan is fully funded at that rate.
                </p>
            </div>

        </div>
    );
};

export default SinkingFundPremium;
