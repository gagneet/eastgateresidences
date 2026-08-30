"use client";

import React from "react";
import {motion} from "framer-motion";
import {Cell, Pie, PieChart, ResponsiveContainer} from "recharts";
import {CHART_INK, CHART_STATUS} from "@/lib/chartTheme";
import {Activity} from "lucide-react";
import InfoButton from "./InfoButton";

interface HealthBreakdown {
    surplus_ratio: number;
    arrears_pct: number;
    cashflow_buffer: number;
    forecast_stability: number;
    budget_discipline: number;
    expense_volatility: number;
}

interface HealthScorePremiumProps {
    score: number;
    breakdown: HealthBreakdown;
    riskLevel: string;
    year: string;
    details?: Record<string, any>;
}

const RISK_CONFIG: Record<string, { chart: string; label: string; bg: string; text: string; border: string }> = {
    excellent: {
        chart: CHART_STATUS.good,
        label: "EXCELLENT",
        bg: "bg-emerald-500/10",
        text: "text-emerald-600",
        border: "border-emerald-500/20"
    },
    good: {chart: CHART_STATUS.good, label: "GOOD", bg: "bg-green-500/10", text: "text-green-600", border: "border-green-500/20"},
    moderate: {
        chart: CHART_STATUS.warning,
        label: "MODERATE",
        bg: "bg-amber-500/10",
        text: "text-amber-600",
        border: "border-amber-500/20"
    },
    at_risk: {
        chart: CHART_STATUS.serious,
        label: "AT RISK",
        bg: "bg-orange-500/10",
        text: "text-orange-600",
        border: "border-orange-500/20"
    },
    critical: {
        chart: CHART_STATUS.critical,
        label: "CRITICAL",
        bg: "bg-rose-500/10",
        text: "text-rose-600",
        border: "border-rose-500/20"
    },
};

const COMPONENTS = [
    {key: "surplus_ratio", label: "Surplus Ratio", max: 20},
    {key: "arrears_pct", label: "Arrears Rate", max: 20},
    {key: "cashflow_buffer", label: "Cashflow Buffer", max: 15},
    {key: "forecast_stability", label: "Forecast Stability", max: 15},
    {key: "budget_discipline", label: "Budget Discipline", max: 15},
    {key: "expense_volatility", label: "Expense Volatility", max: 15},
];
/**
 * @generated FunctionHeader
 * Function: HealthScorePremium
 * Path: frontend/src/components/finance/premium/HealthScorePremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const HealthScorePremium = ({
                                       score,
                                       breakdown,
                                       riskLevel,
                                       year,
                                       details = {},
                                   }: HealthScorePremiumProps) => {
    const cfg = RISK_CONFIG[riskLevel] || RISK_CONFIG.moderate;

    const chartData = [
        {name: "Score", value: Math.round(score)},
        {name: "Remaining", value: 100 - Math.round(score)},
    ];

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            className="p-6 rounded-xl border border-border bg-card shadow-sm flex flex-col h-full group"
        >
            <div className="flex justify-between items-start mb-8">
                <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-foreground text-xl font-semibold tracking-tight">Financial Health</h3>
                        <Activity className="w-5 h-5 text-primary"/>
                        <InfoButton
                            title="Financial Health"
                            description="A composite index representing the building's overall financial stability and fiscal discipline."
                            dataSources={["annual_levies", "unit_levy_ledger", "levy_categories"]}
                            logic={
                                <ul className="list-disc pl-3 space-y-1">
                                    <li>Surplus Ratio (20%): Closing balance relative to total income.</li>
                                    <li>Arrears Rate (20%): Percentage of units with outstanding debt.</li>
                                    <li>Cashflow Buffer (15%): Minimum balance relative to monthly expenses.</li>
                                    <li>Forecast Stability (15%): Variance and predictability of future projections.
                                    </li>
                                    <li>Budget Discipline (15%): Historical accuracy of actual vs budgeted spending.
                                    </li>
                                    <li>Expense Volatility (15%): Year-over-year change in major expense items.</li>
                                </ul>
                            }
                        />
                    </div>
                    <p className="text-muted-foreground text-sm font-medium">Holistic building wellness FY {year}</p>
                </div>
                <div
                    className="px-4 py-2 rounded-2xl border"
                    style={{
                        backgroundColor: `${cfg.chart}1a`,
                        borderColor: `${cfg.chart}33`
                    }}
                >
          <span
              className="text-xs font-semibold uppercase tracking-widest"
              style={{color: cfg.chart}}
          >
            {cfg.label}
          </span>
                </div>
            </div>

            <div className="flex flex-col lg:flex-row gap-8 items-center flex-1">
                {/* Gauge Section */}
                <div className="relative w-48 h-48 shrink-0">
                    {/* Score gauge: the coloured arc is the score, the neutral
                        remainder is the track. Tremor's DonutChart took
                        ["<tier colour>", "slate-100"] for exactly this shape.
                        startAngle 90 / endAngle -270 makes it fill clockwise from
                        12 o'clock, matching Tremor's default sweep. */}
                    <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                            <Pie
                                data={chartData}
                                dataKey="value"
                                nameKey="name"
                                cx="50%"
                                cy="50%"
                                innerRadius="70%"
                                outerRadius="100%"
                                startAngle={90}
                                endAngle={-270}
                                stroke="none"
                                isAnimationActive
                            >
                                <Cell fill={cfg.chart}/>
                                <Cell fill={CHART_INK.grid}/>
                            </Pie>
                        </PieChart>
                    </ResponsiveContainer>
                    <div className="absolute inset-0 flex flex-col items-center justify-center">
                        <motion.span
                            initial={{scale: 0.5, opacity: 0}}
                            animate={{scale: 1, opacity: 1}}
                            transition={{delay: 0.3, type: "spring"}}
                            className="text-5xl font-semibold"
                            style={{color: cfg.chart}}
                        >
                            {Math.round(score)}
                        </motion.span>
                        <span
                            className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Index Score</span>
                    </div>
                </div>

                {/* Breakdown Bars */}
                <div className="flex-1 w-full space-y-4">
                    {COMPONENTS.map(({key, label, max}, idx) => {
                        const val = (breakdown as any)[key] ?? 0;
                        const pct = Math.round((val / max) * 100);
                        return (
                            <motion.div
                                key={key}
                                initial={{opacity: 0, x: -10}}
                                animate={{opacity: 1, x: 0}}
                                transition={{delay: 0.1 * idx}}
                            >
                                <div
                                    className="flex justify-between text-[10px] font-semibold uppercase tracking-tight mb-1">
                                    <span className="text-muted-foreground">{label}</span>
                                    <span className="text-foreground">{val.toFixed(1)} <span
                                        className="text-muted-foreground">/ {max}</span></span>
                                </div>
                                <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                                    <motion.div
                                        initial={{width: 0}}
                                        animate={{width: `${pct}%`}}
                                        transition={{duration: 1, delay: 0.5 + 0.1 * idx}}
                                        className={`h-full rounded-full ${
                                            pct >= 80 ? 'bg-emerald-500' : pct >= 50 ? 'bg-amber-500' : 'bg-rose-500'
                                        }`}
                                    />
                                </div>
                            </motion.div>
                        );
                    })}
                </div>
            </div>

            {Object.keys(details).length > 0 && (
                <div className="mt-8 pt-6 border-t border-border grid grid-cols-3 gap-4">
                    {details.buffer_months !== undefined && (
                        <div className="flex flex-col">
                            <span
                                className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">Buffer</span>
                            <span className="text-sm font-semibold text-foreground">{details.buffer_months} Mo</span>
                        </div>
                    )}
                    {details.arrears_pct_raw !== undefined && (
                        <div className="flex flex-col">
                            <span
                                className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">Arrears</span>
                            <span className="text-sm font-semibold text-foreground">{details.arrears_pct_raw}%</span>
                        </div>
                    )}
                    {details.units_owing !== undefined && (
                        <div className="flex flex-col">
                            <span className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">Debt Units</span>
                            <span className="text-sm font-semibold text-foreground">{details.units_owing}</span>
                        </div>
                    )}
                </div>
            )}
        </motion.div>
    );
};

export default HealthScorePremium;
