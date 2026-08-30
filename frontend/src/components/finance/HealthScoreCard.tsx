"use client";
import React from "react";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {PolarAngleAxis, RadialBar, RadialBarChart, ResponsiveContainer} from "recharts";
import {Activity} from "lucide-react";

interface HealthBreakdown {
    surplus_ratio: number;
    arrears_pct: number;
    cashflow_buffer: number;
    forecast_stability: number;
    budget_discipline: number;
    expense_volatility: number;
}

interface HealthScoreCardProps {
    score: number;
    breakdown: HealthBreakdown;
    risk_level: string;
    year: string;
    details?: Record<string, unknown>;
}

const RISK_CONFIG: Record<string, { color: string; bg: string; label: string }> = {
    excellent: {color: "#22c55e", bg: "bg-green-50", label: "EXCELLENT"},
    good: {color: "#86efac", bg: "bg-green-50", label: "GOOD"},
    moderate: {color: "#f59e0b", bg: "bg-amber-50", label: "MODERATE"},
    at_risk: {color: "#f97316", bg: "bg-orange-50", label: "AT RISK"},
    critical: {color: "#ef4444", bg: "bg-red-50", label: "CRITICAL"},
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
 * Function: HealthScoreCard
 * Path: frontend/src/components/finance/HealthScoreCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const HealthScoreCard: React.FC<HealthScoreCardProps> = ({
                                                                    score, breakdown, risk_level, year, details = {},
                                                                }) => {
    const cfg = RISK_CONFIG[risk_level] || RISK_CONFIG.moderate;
    const gaugeData = [{value: Math.round(score), fill: cfg.color}];

    return (
        <Card data-testid="health-score-card">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Activity className="w-5 h-5 text-emerald-600"/>
                    Financial Health Score
                </CardTitle>
                <CardDescription>Overall financial wellness for {year}</CardDescription>
            </CardHeader>
            <CardContent>
                <div className="flex flex-col md:flex-row gap-6 items-center">
                    {/* Gauge */}
                    <div className="relative w-44 h-44 shrink-0">
                        <ResponsiveContainer width="100%" height="100%">
                            <RadialBarChart
                                cx="50%"
                                cy="50%"
                                innerRadius="70%"
                                outerRadius="100%"
                                startAngle={90}
                                endAngle={-270}
                                data={gaugeData}
                            >
                                <PolarAngleAxis type="number" domain={[0, 100]} angleAxisId={0} tick={false}/>
                                <RadialBar
                                    background={{fill: "#e5e7eb"}}
                                    dataKey="value"
                                    angleAxisId={0}
                                    cornerRadius={8}
                                />
                            </RadialBarChart>
                        </ResponsiveContainer>
                        <div className="absolute inset-0 flex flex-col items-center justify-center">
                            <div className="text-3xl font-black" style={{color: cfg.color}}>
                                {Math.round(score)}
                            </div>
                            <div className="text-xs text-gray-500">/ 100</div>
                            <div
                                className={`mt-1 px-2 py-0.5 rounded text-xs font-bold ${cfg.bg}`}
                                style={{color: cfg.color}}
                            >
                                {cfg.label}
                            </div>
                        </div>
                    </div>

                    {/* Breakdown bars */}
                    <div className="flex-1 w-full space-y-2">
                        {COMPONENTS.map(({key, label, max}) => {
                            const val = (breakdown as any)[key] ?? 0;
                            const pct = Math.round((val / max) * 100);
                            const barColor = pct >= 80 ? "#22c55e" : pct >= 50 ? "#f59e0b" : "#ef4444";
                            return (
                                <div key={key}>
                                    <div className="flex justify-between text-xs mb-0.5">
                                        <span className="text-gray-600">{label}</span>
                                        <span className="font-semibold">{val.toFixed(1)} / {max}</span>
                                    </div>
                                    <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                                        <div
                                            className="h-full rounded-full transition-all duration-700"
                                            style={{width: `${pct}%`, backgroundColor: barColor}}
                                        />
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>

                {/* Key metrics */}
                {Object.keys(details).length > 0 && (
                    <div className="mt-4 grid grid-cols-2 md:grid-cols-3 gap-3 pt-4 border-t">
                        {[
                            {label: "Total Income", key: "total_income"},
                            {label: "Closing Balance", key: "closing_balance"},
                            {label: "Units Owing", key: "units_owing"},
                            {label: "Arrears %", key: "arrears_pct_raw", suffix: "%"},
                            {label: "Buffer Months", key: "buffer_months"},
                            {label: "Total Units", key: "total_units"},
                        ].map(({label, key, suffix}) => {
                            const val = details[key];
                            if (val == null) return null;
                            const isAmount = key.includes("income") || key.includes("balance");
                            return (
                                <div key={key} className="bg-gray-50 rounded p-2 text-center">
                                    <div className="text-xs text-gray-500">{label}</div>
                                    <div className="text-sm font-semibold text-gray-800">
                                        {isAmount
                                            ? `$${Number(val).toLocaleString("en-AU", {maximumFractionDigits: 2})}`
                                            : `${Number(val).toLocaleString()}${suffix || ""}`}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </CardContent>
        </Card>
    );
};

export default HealthScoreCard;
