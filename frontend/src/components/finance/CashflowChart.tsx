"use client";
import React from "react";
import {Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Badge} from "@/components/ui/badge";
import {TrendingDown, TrendingUp} from "lucide-react";

import {formatMoneyCompact} from '@/lib/currency';
interface CashflowMonth {
    month: string;
    month_number: number;
    expected_income: number;
    expected_expenses: number;
    net_cashflow: number;
    cumulative_balance: number;
    is_risk_month: boolean;
}

interface CashflowChartProps {
    months: CashflowMonth[];
    annualIncome: number;
    annualExpenses: number;
    minBalance: number;
    riskMonths: string[];
    openingBalance: number;
    year: string;
}
/**
 * @generated FunctionHeader
 * Function: fmt
 * Path: frontend/src/components/finance/CashflowChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmt = (v: number) =>
    `$${v.toLocaleString("en-AU", {maximumFractionDigits: 0})}`;
/**
 * @generated FunctionHeader
 * Function: CustomDot
 * Path: frontend/src/components/finance/CashflowChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CustomDot = (props: any) => {
    const {cx, cy, payload} = props;
    if (!payload.is_risk_month) return null;
    return <circle cx={cx} cy={cy} r={5} fill="#ef4444" stroke="white" strokeWidth={2}/>;
};
/**
 * @generated FunctionHeader
 * Function: CashflowChart
 * Path: frontend/src/components/finance/CashflowChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const CashflowChart: React.FC<CashflowChartProps> = ({
                                                                months,
                                                                annualIncome,
                                                                annualExpenses,
                                                                minBalance,
                                                                riskMonths,
                                                                openingBalance,
                                                                year,
                                                            }) => {
    const surplus = annualIncome - annualExpenses;

    return (
        <Card data-testid="cashflow-chart">
            <CardHeader>
                <div className="flex items-start justify-between">
                    <div>
                        <CardTitle className="flex items-center gap-2">
                            {surplus >= 0 ? (
                                <TrendingUp className="w-5 h-5 text-emerald-600"/>
                            ) : (
                                <TrendingDown className="w-5 h-5 text-red-500"/>
                            )}
                            Monthly Cashflow Projection
                        </CardTitle>
                        <CardDescription>Cumulative balance throughout {year}</CardDescription>
                    </div>
                    {riskMonths.length > 0 && (
                        <Badge variant="destructive" className="text-xs">
                            {riskMonths.length} risk month{riskMonths.length > 1 ? "s" : ""}
                        </Badge>
                    )}
                </div>
            </CardHeader>
            <CardContent>
                {/* Summary */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                    {[
                        {label: "Opening Balance", value: openingBalance, color: "text-gray-700"},
                        {label: "Annual Income", value: annualIncome, color: "text-emerald-700"},
                        {label: "Annual Expenses", value: annualExpenses, color: "text-red-600"},
                        {
                            label: "Surplus / Deficit",
                            value: surplus,
                            color: surplus >= 0 ? "text-emerald-700" : "text-red-600"
                        },
                    ].map((m) => (
                        <div key={m.label} className="bg-gray-50 rounded-lg p-2 text-center">
                            <div className="text-xs text-gray-500">{m.label}</div>
                            <div className={`text-sm font-bold ${m.color}`}>{fmt(m.value)}</div>
                        </div>
                    ))}
                </div>

                <ResponsiveContainer width="100%" height={240}>
                    <AreaChart data={months} margin={{top: 10, right: 10, bottom: 0, left: 10}}>
                        <defs>
                            <linearGradient id="balanceGrad" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#2F4F4F" stopOpacity={0.3}/>
                                <stop offset="95%" stopColor="#2F4F4F" stopOpacity={0.05}/>
                            </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb"/>
                        <XAxis dataKey="month" tick={{fontSize: 9}} angle={-30} textAnchor="end"/>
                        <YAxis tickFormatter={(v) => formatMoneyCompact(v)} tick={{fontSize: 9}}/>
                        <Tooltip
                            formatter={(v: number | undefined): React.ReactNode => fmt(v ?? 0)}
                            contentStyle={{fontSize: 11}}
                        />
                        <ReferenceLine y={0} stroke="#ef4444" strokeDasharray="4 4"
                                       label={{value: "Zero", fontSize: 9, fill: "#ef4444"}}/>
                        <Area
                            type="monotone"
                            dataKey="cumulative_balance"
                            stroke="#2F4F4F"
                            strokeWidth={2}
                            fill="url(#balanceGrad)"
                            name="Balance"
                            dot={<CustomDot/>}
                        />
                    </AreaChart>
                </ResponsiveContainer>

                {riskMonths.length > 0 && (
                    <div className="mt-3 p-2 bg-red-50 border border-red-200 rounded text-xs">
                        <span className="font-semibold text-red-700">Risk months (negative balance): </span>
                        <span className="text-red-600">{riskMonths.join(", ")}</span>
                    </div>
                )}
                <div className="mt-2 text-xs text-gray-500">
                    Minimum projected balance: <span className="font-semibold"
                                                     style={{color: minBalance < 0 ? "#ef4444" : "#2F4F4F"}}>{fmt(minBalance)}</span>
                </div>
            </CardContent>
        </Card>
    );
};

export default CashflowChart;
