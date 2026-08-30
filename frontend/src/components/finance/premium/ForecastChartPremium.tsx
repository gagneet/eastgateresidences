"use client";

import React, {useState} from "react";
import {motion} from "framer-motion";
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
import {Tabs, TabsList, TabsTrigger} from "@/components/ui/tabs";
import {Badge} from "@/components/ui/badge";
import {Download, TrendingDown, TrendingUp} from "lucide-react";
import {formatCurrency} from "@/lib/utils";
import InfoButton from "./InfoButton";

import {formatMoneyCompact} from '@/lib/currency';
interface ForecastItem {
    category: string;
    fund_type: string;
    projection_year_1: number;
    projection_year_2: number;
    projection_year_3: number;
    method: string;
    confidence_score: number;
}

interface CategoryItem {
    name: string;
    fund_type: string;
    budgeted_amount: number;
    actual_amount: number;
}

interface ForecastChartPremiumProps {
    forecasts: ForecastItem[];
    year: string;
    categories?: CategoryItem[];
}
// Custom tooltip for the chart
/**
 * @generated FunctionHeader
 * Function: ChartTooltip
 * Path: frontend/src/components/finance/premium/ForecastChartPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ChartTooltip = ({
                          active, payload, label, year, hasActuals, variance, variancePct
                      }: any) => {
    if (!active || !payload?.length) return null;
    const isBaseYear = label === `FY ${year}`;
    return (
        <div
            className="bg-popover border border-border rounded-xl p-4 shadow-md text-xs min-w-[200px]">
            <p className="font-semibold text-foreground text-sm mb-3">
                {label}
                {isBaseYear && (
                    <span
                        className="ml-2 text-[9px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded-full font-semibold uppercase">Base Year</span>
                )}
                {!isBaseYear && (
                    <span
                        className="ml-2 text-[9px] bg-primary/10 text-primary px-1.5 py-0.5 rounded-full font-semibold uppercase">Projected</span>
                )}
            </p>
            {payload.map((entry: any) =>
                entry.value != null ? (
                    <div key={entry.dataKey} className="flex justify-between gap-6 py-0.5">
                        <span style={{color: entry.color}} className="font-bold">{entry.name}</span>
                        <span className="font-semibold text-foreground">{formatCurrency(entry.value)}</span>
                    </div>
                ) : null
            )}
            {isBaseYear && hasActuals && variance !== 0 && (
                <div
                    className={`mt-2 pt-2 border-t border-border text-[10px] font-bold ${variance > 0 ? "text-rose-600" : "text-emerald-600"}`}>
                    {variance > 0 ? "↑ Over budget by " : "↓ Under budget by "}
                    {formatCurrency(Math.abs(variance))} ({Math.abs(variancePct).toFixed(1)}%)
                </div>
            )}
        </div>
    );
};
/**
 * @generated FunctionHeader
 * Function: ForecastChartPremium
 * Path: frontend/src/components/finance/premium/ForecastChartPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const ForecastChartPremium = ({forecasts, year, categories = []}: ForecastChartPremiumProps) => {
    const [fundFilter, setFundFilter] = useState<"all" | "administrative" | "sinking">("all");

    const filtered = forecasts.filter(
        (f) => fundFilter === "all" || f.fund_type === fundFilter
    );
    const filteredCats = categories.filter(
        (c) => fundFilter === "all" || c.fund_type === fundFilter
    );

    const y1 = parseInt(year) + 1;
    const y2 = parseInt(year) + 2;
    const y3 = parseInt(year) + 3;

    // ── Base year budget vs actual ────────────────────────────────────────────
    const budgetedTotal = filteredCats.reduce((s, c) => s + (c.budgeted_amount || 0), 0);
    const actualTotal = filteredCats.reduce((s, c) => s + (c.actual_amount || 0), 0);
    const hasActuals = actualTotal > 0;
    const variance = actualTotal - budgetedTotal;
    const variancePct = budgetedTotal > 0 ? (variance / budgetedTotal) * 100 : 0;

    // ── Forecast totals ───────────────────────────────────────────────────────
    const y1Total = filtered.reduce((s, f) => s + (f.projection_year_1 || 0), 0);
    const y2Total = filtered.reduce((s, f) => s + (f.projection_year_2 || 0), 0);
    const y3Total = filtered.reduce((s, f) => s + (f.projection_year_3 || 0), 0);

    // Growth vs anchor (base year actual if available, else budget)
    const anchor = hasActuals ? actualTotal : budgetedTotal > 0 ? budgetedTotal : y1Total;
    const growth = anchor > 0 ? ((y3Total - anchor) / anchor) * 100 : 0;

    // ── Chart data: base year bars + forecast line ────────────────────────────
    const chartData = [
        {
            name: `FY ${year}`,
            Budget: budgetedTotal || null,
            Actual: hasActuals ? actualTotal : null,
            Forecast: null as number | null,
        },
        {name: `FY ${y1}`, Budget: null, Actual: null, Forecast: y1Total || null},
        {name: `FY ${y2}`, Budget: null, Actual: null, Forecast: y2Total || null},
        {name: `FY ${y3}`, Budget: null, Actual: null, Forecast: y3Total || null},
    ];
    /**
     * @generated FunctionHeader
     * Function: handleExportCSV
     * Path: frontend/src/components/finance/premium/ForecastChartPremium.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleExportCSV = () => {
        const rows = [
            ["Category", "Fund", "Method", "Confidence", "Y+1", "Y+2", "Y+3"],
            ...filtered.map((f) => [
                f.category ?? "", f.fund_type ?? "", f.method ?? "",
                `${Math.round((f.confidence_score ?? 0) * 100)}%`,
                f.projection_year_1 ?? 0, f.projection_year_2 ?? 0, f.projection_year_3 ?? 0,
            ]),
        ];
        const csv = rows.map((r) => r.join(",")).join("\n");
        const blob = new Blob([csv], {type: "text/csv"});
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `forecast_${year}.csv`;
        a.click();
    };

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            className="p-6 rounded-xl border border-border bg-card shadow-sm flex flex-col h-full group"
        >
            {/* Header */}
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-6">
                <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-foreground text-xl font-semibold tracking-tight">Strategic Forecasting</h3>
                        <TrendingUp className="w-5 h-5 text-emerald-600"/>
                        <InfoButton
                            title="Strategic Forecasting"
                            description="Shows FY base year budget vs actual spend, then a 3-year forward projection. Use this to assess forecast accuracy and plan future levy rates."
                            dataSources={["financial_forecasts", "levy_categories", "site_settings (CPI)"]}
                            logic={
                                <div className="space-y-2">
                                    <p><span className="font-bold text-foreground">Base Year Bars:</span> Green =
                                        approved budget, Purple = actual spend. Gap = overspend/underspend.</p>
                                    <p><span className="font-bold text-foreground">Forecast Line (emerald):</span> 3-year
                                        projection using the best-fit model per category:</p>
                                    <ul className="list-disc pl-3 space-y-1">
                                        <li><span className="font-bold text-primary">Linear:</span> Trend from 3+
                                            years of actuals (R² &gt; 0.6 required).
                                        </li>
                                        <li><span className="font-bold text-primary">Inflation:</span> 3.5% CPI
                                            compounding year-on-year.
                                        </li>
                                        <li><span className="font-bold text-primary">Capital Works:</span> Spreads
                                            known large capital items across the period.
                                        </li>
                                    </ul>
                                </div>
                            }
                        />
                    </div>
                    <p className="text-muted-foreground text-sm font-medium">FY {year} accuracy check + 3-year expenditure
                        projection</p>
                </div>

                <div className="flex items-center gap-3">
                    {/* Tremor's TabGroup addressed tabs by NUMERIC INDEX, so this
                        carried a two-way index<->string translation. shadcn Tabs are
                        addressed by value, so the state string binds directly and the
                        translation disappears — same behaviour, one less thing that
                        can drift out of order if a tab is ever inserted. */}
                    <Tabs value={fundFilter} onValueChange={(v) => setFundFilter(v as any)}>
                        <TabsList>
                            <TabsTrigger value="all" className="text-[10px] font-semibold uppercase tracking-widest px-4 py-1.5 rounded-lg">All</TabsTrigger>
                            <TabsTrigger value="administrative" className="text-[10px] font-semibold uppercase tracking-widest px-4 py-1.5 rounded-lg">Admin</TabsTrigger>
                            <TabsTrigger value="sinking" className="text-[10px] font-semibold uppercase tracking-widest px-4 py-1.5 rounded-lg">Sinking</TabsTrigger>
                        </TabsList>
                    </Tabs>

                    <button
                        onClick={handleExportCSV}
                        className="p-2.5 rounded-xl bg-card border border-border text-muted-foreground hover:text-primary hover:border-primary/20 transition-all shadow-sm"
                    >
                        <Download className="w-4 h-4"/>
                    </button>
                </div>
            </div>

            {/* ── Base year accuracy strip ── */}
            {(budgetedTotal > 0 || hasActuals) && (
                <div className="grid grid-cols-3 gap-3 mb-5 p-4 rounded-2xl bg-muted border border-border">
                    <div>
                        <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">FY {year} Budget</p>
                        <p className="text-lg font-semibold text-muted-foreground">{formatCurrency(budgetedTotal)}</p>
                    </div>
                    <div>
                        <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">FY {year} Actual</p>
                        <p className={`text-lg font-semibold ${hasActuals ? "text-foreground" : "text-muted-foreground"}`}>
                            {hasActuals ? formatCurrency(actualTotal) : "No actuals yet"}
                        </p>
                    </div>
                    <div>
                        <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">Variance</p>
                        {hasActuals && budgetedTotal > 0 ? (
                            <div className="flex items-center gap-2 flex-wrap">
                                <p className={`text-lg font-semibold ${variance > 0 ? "text-rose-600" : "text-emerald-600"}`}>
                                    {variance > 0 ? "+" : ""}{formatCurrency(variance)}
                                </p>
                                <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${
                                    variance > 0
                                        ? "bg-rose-50 text-rose-600 border-rose-100"
                                        : "bg-emerald-50 text-emerald-600 border-emerald-100"
                                }`}>
                  {variance > 0 ? <TrendingUp className="inline w-3 h-3 mr-0.5"/> :
                      <TrendingDown className="inline w-3 h-3 mr-0.5"/>}
                                    {variancePct > 0 ? "+" : ""}{variancePct.toFixed(1)}%
                </span>
                            </div>
                        ) : (
                            <p className="text-lg font-semibold text-muted-foreground">—</p>
                        )}
                    </div>
                </div>
            )}

            {/* ── Forecast summary cards ── */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                {[
                    {label: "Year +1", year: y1, total: y1Total},
                    {label: "Year +2", year: y2, total: y2Total},
                    {label: "Year +3", year: y3, total: y3Total},
                ].map((t) => (
                    <div key={t.label} className="p-4 rounded-2xl bg-primary/10 border border-primary/20">
                        <div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">
                            {t.label} • FY {t.year}
                        </div>
                        <div className="text-xl font-semibold text-primary">{formatCurrency(t.total)}</div>
                    </div>
                ))}
            </div>

            {/* ── Combined chart ── */}
            <div style={{minHeight: 280}}>
                <ResponsiveContainer width="100%" height={280}>
                    <ComposedChart data={chartData} margin={{top: 5, right: 10, left: 0, bottom: 5}}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9"/>
                        <XAxis
                            dataKey="name"
                            tick={{fontSize: 11, fontWeight: 700, fill: "#64748b"}}
                            axisLine={false}
                            tickLine={false}
                        />
                        <YAxis
                            tickFormatter={(v) => formatMoneyCompact(v)}
                            tick={{fontSize: 10, fill: "#94a3b8"}}
                            axisLine={false}
                            tickLine={false}
                            width={55}
                        />
                        <Tooltip
                            content={
                                <ChartTooltip
                                    year={year}
                                    hasActuals={hasActuals}
                                    variance={variance}
                                    variancePct={variancePct}
                                />
                            }
                        />
                        <Legend wrapperStyle={{fontSize: 11, fontWeight: 700, paddingTop: 12}}/>

                        {/* Reference line separating base year from forecast */}
                        <ReferenceLine
                            x={`FY ${year}`}
                            stroke="#cbd5e1"
                            strokeDasharray="4 4"
                            strokeWidth={1.5}
                        />

                        {/* Base year bars */}
                        <Bar dataKey="Budget" name="Budget" fill="#94a3b8" radius={[4, 4, 0, 0]} maxBarSize={44}/>
                        <Bar dataKey="Actual" name="Actual" fill="#6366f1" radius={[4, 4, 0, 0]} maxBarSize={44}/>

                        {/* 3-year forecast line */}
                        <Line
                            type="monotone"
                            dataKey="Forecast"
                            name="3-Yr Forecast"
                            stroke="#10b981"
                            strokeWidth={2.5}
                            dot={{r: 4, fill: "#10b981", strokeWidth: 0}}
                            activeDot={{r: 6, fill: "#10b981", stroke: "white", strokeWidth: 2}}
                            connectNulls
                        />
                    </ComposedChart>
                </ResponsiveContainer>
            </div>

            {/* Footer */}
            <div className="mt-6 flex justify-between items-center">
                <div className="flex items-center gap-3">
                    <div className={`px-3 py-1 rounded-full text-[10px] font-semibold uppercase tracking-widest border ${
                        growth > 0
                            ? "bg-rose-50 text-rose-600 border-rose-100"
                            : "bg-emerald-50 text-emerald-600 border-emerald-100"
                    }`}>
                        {growth > 0 ? "+" : ""}{growth.toFixed(1)}% base→Y+3
                    </div>
                    <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-tighter">
            {filtered.length} forecast categories
          </span>
                </div>

                <div className="flex gap-2">
                    {Array.from(new Set(filtered.map(f => f.method))).map(method => (
                        <Badge key={method} variant="secondary" className="text-[9px] font-semibold uppercase">
                            {method}
                        </Badge>
                    ))}
                </div>
            </div>
        </motion.div>
    );
};

export default ForecastChartPremium;
