"use client";

import React from "react";
import {motion} from "framer-motion";
// NOTE: lucide-react also exports `PieChart` (the icon used in this header) and
// recharts exports a `PieChart` container. Aliasing the recharts one keeps the
// icon working — without the alias the later import silently wins and the header
// icon renders as an empty chart container.
import {Cell, Pie, PieChart as RechartsPieChart, ResponsiveContainer, Tooltip} from "recharts";
import {PieChart, TrendingDown} from "lucide-react";
import {seriesColor, tooltipProps} from "@/lib/chartTheme";
import {formatCurrency} from "@/lib/utils";
import InfoButton from "./InfoButton";

interface CategoryRow {
    name: string;
    fund_type: string;
    budgeted_amount: number;
    actual_amount: number;
}

interface ExpenseConcentrationPremiumProps {
    categories: CategoryRow[];
    year: string;
}
/**
 * @generated FunctionHeader
 * Function: ExpenseConcentrationPremium
 * Path: frontend/src/components/finance/premium/ExpenseConcentrationPremium.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const ExpenseConcentrationPremium = ({categories, year}: ExpenseConcentrationPremiumProps) => {
    // Use actual if available, else budgeted
    const data = categories
        .map(c => ({
            name: c.name,
            value: c.actual_amount || c.budgeted_amount,
            fund: c.fund_type
        }))
        .filter(item => item.value > 0)
        .sort((a, b) => b.value - a.value);

    const top5 = data.slice(0, 5);
    const othersValue = data.slice(5).reduce((acc, item) => acc + item.value, 0);

    const chartData = [...top5];
    if (othersValue > 0) {
        chartData.push({name: "Others", value: othersValue, fund: "mixed"});
    }

    const totalExpenses = data.reduce((acc, item) => acc + item.value, 0);

    // Tremor took colour NAMES ("indigo", "cyan", …) and resolved them to its own
    // CSS variables. Those are generated at runtime and purged by Tailwind v4,
    // which is why globals.css carried a 473-line workaround. The slice colour and
    // the legend dot now come from the same validated palette, so they cannot
    // drift apart.
    const sliceColor = (idx: number) => seriesColor(idx);

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            className="p-6 rounded-xl border border-border bg-card shadow-sm flex flex-col h-full group"
        >
            <div className="flex justify-between items-start mb-8">
                <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-foreground text-xl font-semibold tracking-tight">Expense Concentration</h3>
                        <PieChart className="w-5 h-5 text-primary"/>
                        <InfoButton
                            title="Expense Concentration"
                            description="Visual breakdown of where building funds are being allocated, focusing on the top 5 largest expenditure items."
                            dataSources={["levy_categories", "financial_transactions"]}
                            logic="Categories are aggregated by actual spending (if available) or budgeted amounts. The 'Others' segment combines all categories outside the top 5 to provide a clear view of spending tail risks."
                        />
                    </div>
                    <p className="text-muted-foreground text-sm font-medium">Distribution of major expenditures</p>
                </div>
            </div>

            <div className="flex flex-col lg:flex-row gap-8 items-center flex-1">
                <div className="w-48 h-48 shrink-0">
                    <ResponsiveContainer width="100%" height="100%">
                        <RechartsPieChart>
                            <Pie
                                data={chartData}
                                dataKey="value"
                                nameKey="name"
                                cx="50%"
                                cy="50%"
                                // innerRadius is what makes this a donut rather than
                                // a pie — Tremor's DonutChart had it baked in.
                                innerRadius="60%"
                                outerRadius="100%"
                                paddingAngle={2}
                                stroke="none"
                                isAnimationActive
                            >
                                {chartData.map((entry, idx) => (
                                    <Cell key={entry.name} fill={sliceColor(idx)}/>
                                ))}
                            </Pie>
                            <Tooltip
                                {...tooltipProps}
                                formatter={(v: any, name: any) => [formatCurrency(Number(v)), name]}
                            />
                        </RechartsPieChart>
                    </ResponsiveContainer>
                </div>

                <div className="flex-1 w-full">
                    <ul className="divide-y divide-border">
                        {chartData.map((item, idx) => (
                            <li key={item.name} className="flex items-center justify-between py-2.5">
                                <div className="flex items-center gap-3">
                                    <div
                                        className="w-2 h-2 rounded-full"
                                        style={{backgroundColor: sliceColor(idx)}}
                                    />
                                    <span className="text-xs font-bold text-foreground">{item.name}</span>
                                </div>
                                <div className="flex flex-col items-end">
                                    <span
                                        className="text-xs font-semibold text-foreground">{formatCurrency(item.value)}</span>
                                    <span className="text-[9px] font-semibold text-muted-foreground uppercase tracking-tighter">
                      {totalExpenses > 0 ? ((item.value / totalExpenses) * 100).toFixed(1) : '0.0'}%
                    </span>
                                </div>
                            </li>
                        ))}
                    </ul>
                </div>
            </div>

            <div className="mt-8 pt-6 border-t border-border">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <TrendingDown className="w-4 h-4 text-emerald-500"/>
                        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Total Expenditure:</span>
                    </div>
                    <span className="text-sm font-semibold text-foreground">{formatCurrency(totalExpenses)}</span>
                </div>
            </div>
        </motion.div>
    );
};

export default ExpenseConcentrationPremium;
