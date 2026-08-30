"use client";

import React, {useState} from "react";
import {AnimatePresence, motion} from "framer-motion";
import {Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import {Tabs, TabsList, TabsTrigger} from "@/components/ui/tabs";
import {Badge} from "@/components/ui/badge";
import {CHART_SERIES, axisProps, barRadius, gridProps, tooltipProps} from "@/lib/chartTheme";
import {AlertCircle, Building2, User} from "lucide-react";
import {formatCurrency} from "@/lib/utils";
import InfoButton from "./InfoButton";

interface LotSummary {
    lot_number: string;
    total_admin_paid: number;
    total_sinking_paid: number;
    total_council: number;
    total_water: number;
    total_land_tax: number;
    total_interest: number;
    total_cost: number;
    cost_per_uoe: number;
    arrears_flag: boolean;
    risk_flag: boolean;
}

interface LotCostDistributionPremiumProps {
    summaries: LotSummary[];
    year: string;
}
/**
 * @generated FunctionHeader
 * Function: LotCostDistributionPremium
 * Path: frontend/src/components/finance/premium/LotCostDistributionPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const LotCostDistributionPremium = ({
                                               summaries,
                                               year,
                                           }: LotCostDistributionPremiumProps) => {
    const [viewMode, setViewMode] = useState<"top10" | "flagged" | "all">("top10");
    const [selected, setSelected] = useState<LotSummary | null>(null);

    const filtered = (() => {
        const sorted = [...summaries].sort((a, b) => b.total_cost - a.total_cost);
        if (viewMode === "top10") return sorted.slice(0, 10);
        if (viewMode === "flagged") return sorted.filter((s) => s.arrears_flag || s.risk_flag);
        return sorted.slice(0, 30);
    })();

    const chartData = filtered.map((s) => ({
        name: `Unit ${s.lot_number.replace('LOT', '')}`,
        "Levy": Math.round(s.total_admin_paid + s.total_sinking_paid),
        "Utility/Tax": Math.round(s.total_council + s.total_land_tax + s.total_water),
        "Interest": Math.round(s.total_interest),
        raw: s,
    }));

    const arrearsCount = summaries.filter((s) => s.arrears_flag).length;

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            className="p-6 rounded-xl border border-border bg-card shadow-sm flex flex-col h-full group"
        >
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-8">
                <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-foreground text-xl font-semibold tracking-tight">Cost Distribution</h3>
                        <Building2 className="w-5 h-5 text-primary"/>
                        <InfoButton
                            title="Cost Distribution"
                            description="A forensic breakdown of the 'True Cost of Ownership' for each individual unit, combining direct levies with indirect costs like utilities and taxes."
                            dataSources={["unit_levy_ledger", "water_bills", "council_rates", "land_tax"]}
                            logic="Total cost is calculated by summing all levies paid, pro-rata council rates based on unit entitlements, and actual water consumption (if metered) or shared allocation. 'Impact per UOE' provides a normalized metric for comparing units of different sizes."
                        />
                    </div>
                    <p className="text-muted-foreground text-sm font-medium">True cost of ownership by unit</p>
                </div>

                <div className="flex items-center gap-3">
                    {/* Tremor TabGroup was index-addressed; shadcn Tabs bind the
                        state string directly (see ForecastChartPremium). */}
                    <Tabs value={viewMode} onValueChange={(v) => setViewMode(v as any)}>
                        <TabsList>
                            <TabsTrigger value="top10" className="text-[10px] font-semibold uppercase tracking-widest px-4 py-1.5 rounded-lg">Top 10</TabsTrigger>
                            <TabsTrigger value="flagged" className="text-[10px] font-semibold uppercase tracking-widest px-4 py-1.5 rounded-lg">Flagged</TabsTrigger>
                            <TabsTrigger value="all" className="text-[10px] font-semibold uppercase tracking-widest px-4 py-1.5 rounded-lg">All Units</TabsTrigger>
                        </TabsList>
                    </Tabs>
                </div>
            </div>

            <div className="flex-1 min-h-[300px]">
                <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                        data={chartData}
                        margin={{top: 4, right: 8, bottom: 0, left: 0}}
                        // Tremor's onValueChange fired with the clicked datum; recharts
                        // reports the active label on the chart, so the lookup is by
                        // name exactly as before. Click-to-select behaviour preserved.
                        onClick={(e: any) => {
                            const label = e?.activeLabel;
                            const item = filtered.find(
                                (sItem) => `Unit ${sItem.lot_number.replace("LOT", "")}` === label,
                            );
                            if (item) setSelected(item);
                        }}
                    >
                        <CartesianGrid {...gridProps} />
                        <XAxis {...axisProps} dataKey="name"/>
                        <YAxis {...axisProps} tickFormatter={(v) => formatCurrency(Number(v))}/>
                        <Tooltip {...tooltipProps} formatter={(v: any) => formatCurrency(Number(v))}/>
                        <Legend />
                        {/* stackId groups the three series into one stacked bar, which
                            is what Tremor's stack={true} did. Only the TOP segment gets
                            a rounded cap, or the radius shows through the stack. */}
                        <Bar dataKey="Levy" stackId="a" fill={CHART_SERIES[0]}/>
                        <Bar dataKey="Utility/Tax" stackId="a" fill={CHART_SERIES[2]}/>
                        <Bar dataKey="Interest" stackId="a" fill={CHART_SERIES[3]} radius={barRadius}/>
                    </BarChart>
                </ResponsiveContainer>
            </div>

            <AnimatePresence>
                {selected ? (
                    <motion.div
                        initial={{opacity: 0, height: 0}}
                        animate={{opacity: 1, height: "auto"}}
                        exit={{opacity: 0, height: 0}}
                        className="mt-8 overflow-hidden"
                    >
                        <div className="p-6 rounded-xl bg-primary text-primary-foreground shadow-sm relative">
                            <button
                                onClick={() => setSelected(null)}
                                className="absolute top-4 right-4 text-muted-foreground hover:text-primary-foreground transition-colors"
                            >
                                ×
                            </button>
                            <div className="flex items-center gap-4 mb-4">
                                <div className="w-10 h-10 rounded-xl bg-card/10 flex items-center justify-center">
                                    <User className="w-5 h-5 text-primary"/>
                                </div>
                                <div>
                                    <h4 className="text-lg font-semibold tracking-tight">Unit {selected.lot_number} Detailed
                                        Forensic</h4>
                                    <div className="flex gap-2 mt-1">
                                        {selected.arrears_flag && <Badge variant="destructive"
                                                                         className="text-[8px] font-semibold uppercase">Arrears</Badge>}
                                        {selected.risk_flag &&
                                            <Badge variant="secondary" className="text-[8px] font-semibold uppercase">Risk
                                                Flag</Badge>}
                                    </div>
                                </div>
                            </div>

                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                <div>
                                    <p className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">Levies
                                        Paid</p>
                                    <p className="text-sm font-bold">{formatCurrency(selected.total_admin_paid + selected.total_sinking_paid)}</p>
                                </div>
                                <div>
                                    <p className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">Utilities / Tax</p>
                                    <p className="text-sm font-bold">{formatCurrency(selected.total_water + selected.total_council + selected.total_land_tax)}</p>
                                </div>
                                <div>
                                    <p className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">Cost
                                        / UOE</p>
                                    <p className="text-sm font-bold">{formatCurrency(selected.cost_per_uoe)}</p>
                                </div>
                                <div className="p-2 rounded-xl bg-card/5 border border-white/10">
                                    <p className="text-[9px] font-semibold uppercase tracking-widest text-primary mb-1">Total
                                        Impact</p>
                                    <p className="text-sm font-semibold text-primary">{formatCurrency(selected.total_cost)}</p>
                                </div>
                            </div>
                        </div>
                    </motion.div>
                ) : (
                    <div className="mt-8 flex items-center justify-between pt-6 border-t border-border">
                        <div className="flex gap-4">
                            <div className="flex items-center gap-2">
                                <div className="w-2 h-2 rounded-full bg-primary"/>
                                <span
                                    className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Levy</span>
                            </div>
                            <div className="flex items-center gap-2">
                                <div className="w-2 h-2 rounded-full bg-muted-foreground"/>
                                <span
                                    className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Utilities</span>
                            </div>
                        </div>
                        {arrearsCount > 0 && (
                            <div className="flex items-center gap-2">
                                <AlertCircle className="w-4 h-4 text-rose-500"/>
                                <span
                                    className="text-[10px] font-semibold text-rose-600 uppercase tracking-widest">{arrearsCount} Units in Arrears</span>
                            </div>
                        )}
                    </div>
                )}
            </AnimatePresence>
        </motion.div>
    );
};

export default LotCostDistributionPremium;
