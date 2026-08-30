"use client";

import React from "react";
import {motion} from "framer-motion";
import {Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import {CHART_SERIES, axisProps, gridProps, tooltipProps} from "@/lib/chartTheme";
import {AlertCircle, DollarSign, TrendingUp} from "lucide-react";
import {formatCurrency} from "@/lib/utils";
import InfoButton from "./InfoButton";

interface CashflowMonth {
    month: string;
    month_number: number;
    expected_income: number;
    expected_expenses: number;
    net_cashflow: number;
    cumulative_balance: number;
    is_risk_month: boolean;
}

interface CashflowChartPremiumProps {
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
 * Function: CashflowChartPremium
 * Path: frontend/src/components/finance/premium/CashflowChartPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const CashflowChartPremium = ({
                                         months,
                                         annualIncome,
                                         annualExpenses,
                                         minBalance,
                                         riskMonths,
                                         openingBalance,
                                         year,
                                     }: CashflowChartPremiumProps) => {
    const surplus = annualIncome - annualExpenses;

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            className="p-6 rounded-xl border border-border bg-card shadow-sm flex flex-col h-full group"
        >
            <div className="flex justify-between items-start mb-8">
                <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-foreground text-xl font-semibold tracking-tight">Liquidity & Cashflow</h3>
                        <DollarSign className="w-5 h-5 text-primary"/>
                        <InfoButton
                            title="Liquidity & Cashflow"
                            description="Real-time projection of monthly bank balances, accounting for scheduled levy income and even distribution of estimated expenses."
                            dataSources={["annual_levies", "levy_categories", "payment_schedule"]}
                            logic="Levy income is assumed to arrive at the start of each quarter (Jul, Oct, Jan, Apr) as per the Australian financial year. Expenses are distributed linearly. A 'Risk Period' is triggered if the projected cumulative balance falls below $0 at any point."
                        />
                    </div>
                    <p className="text-muted-foreground text-sm font-medium">Monthly balance projection for {year}</p>
                </div>

                {riskMonths.length > 0 ? (
                    <div className="flex items-center gap-2 px-4 py-2 bg-rose-50 border border-rose-100 rounded-2xl">
                        <AlertCircle className="w-4 h-4 text-rose-500"/>
                        <span className="text-[10px] font-semibold uppercase tracking-widest text-rose-600">
              {riskMonths.length} Risk Period{riskMonths.length > 1 ? 's' : ''}
            </span>
                    </div>
                ) : (
                    <div
                        className="flex items-center gap-2 px-4 py-2 bg-emerald-50 border border-emerald-100 rounded-2xl">
                        <TrendingUp className="w-4 h-4 text-emerald-500"/>
                        <span className="text-[10px] font-semibold uppercase tracking-widest text-emerald-600">
              Healthy Liquidity
            </span>
                    </div>
                )}
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
                <div className="p-4 rounded-2xl bg-muted border border-border">
                    <p className="text-[9px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">Opening</p>
                    <p className="text-sm font-semibold text-foreground">{formatCurrency(openingBalance)}</p>
                </div>
                <div className="p-4 rounded-2xl bg-emerald-50/50 border border-emerald-100">
                    <p className="text-[9px] font-semibold uppercase tracking-widest text-emerald-500 mb-1">Income</p>
                    <p className="text-sm font-semibold text-emerald-700">{formatCurrency(annualIncome)}</p>
                </div>
                <div className="p-4 rounded-2xl bg-rose-50/50 border border-rose-100">
                    <p className="text-[9px] font-semibold uppercase tracking-widest text-rose-500 mb-1">Expenses</p>
                    <p className="text-sm font-semibold text-rose-700">{formatCurrency(annualExpenses)}</p>
                </div>
                <div
                    className={`p-4 rounded-2xl border ${surplus >= 0 ? 'bg-primary/10 border-primary/20' : 'bg-rose-50/50 border-rose-100'}`}>
                    <p className={`text-[9px] font-semibold uppercase tracking-widest mb-1 ${surplus >= 0 ? 'text-primary' : 'text-rose-500'}`}>
                        Net Position
                    </p>
                    <p className={`text-sm font-semibold ${surplus >= 0 ? 'text-primary' : 'text-rose-700'}`}>
                        {formatCurrency(surplus)}
                    </p>
                </div>
            </div>

            <div className="flex-1 min-h-[250px]">
                <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={months} margin={{top: 4, right: 8, bottom: 0, left: 0}}>
                        <defs>
                            <linearGradient id="monthFill" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stopColor={CHART_SERIES[0]} stopOpacity={0.25}/>
                                <stop offset="100%" stopColor={CHART_SERIES[0]} stopOpacity={0}/>
                            </linearGradient>
                        </defs>
                        <CartesianGrid {...gridProps} />
                        <XAxis {...axisProps} dataKey="month"/>
                        <YAxis {...axisProps} tickFormatter={(v) => formatCurrency(Number(v))}/>
                        <Tooltip {...tooltipProps} formatter={(v: any) => formatCurrency(Number(v))}/>
                        <Area
                            type="monotone"
                            dataKey="cumulative_balance"
                            stroke={CHART_SERIES[0]}
                            strokeWidth={2}
                            fill="url(#monthFill)"
                            dot={false}
                            isAnimationActive
                        />
                    </AreaChart>
                </ResponsiveContainer>
            </div>

            <div className="mt-8 flex items-center justify-between pt-6 border-t border-border">
                <div className="flex items-center gap-2">
                    <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Min. Projected Balance:</span>
                    <span className={`text-sm font-semibold ${minBalance < 0 ? 'text-rose-600' : 'text-foreground'}`}>
            {formatCurrency(minBalance)}
          </span>
                </div>

                {riskMonths.length > 0 && (
                    <div className="text-[9px] font-bold text-rose-500 uppercase tracking-tight">
                        Critical low: {riskMonths.join(", ")}
                    </div>
                )}
            </div>
        </motion.div>
    );
};

export default CashflowChartPremium;
