"use client";

import React from "react";
import {motion} from "framer-motion";
import {Area, AreaChart, ResponsiveContainer} from "recharts";
import {CHART_SERIES} from "@/lib/chartTheme";
import {Activity, ArrowUpRight} from "lucide-react";
import {cn} from "@/lib/utils";

interface MarketPulseProps {
    data?: any[];
    suburb?: string;
    medianPrice?: string;
    growth?: string;
    onClick?: () => void;
}

const chartdata = [
    {month: "Jan 25", price: 650000},
    {month: "Feb 25", price: 655000},
    {month: "Mar 25", price: 670000},
    {month: "Apr 25", price: 675000},
    {month: "May 25", price: 680000},
    {month: "Jun 25", price: 685000},
    {month: "Jul 25", price: 690000},
    {month: "Aug 25", price: 700000},
];
/**
 * @generated FunctionHeader
 * Function: MarketPulseCard
 * Path: frontend/src/components/dashboard/premium/MarketPulseCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const MarketPulseCard = ({
                                    suburb = "Denman Prospect",
                                    medianPrice = "$700,000",
                                    growth = "+22.4%",
                                    data = chartdata,
                                    onClick
                                }: MarketPulseProps) => {

    return (
        <motion.div
            initial={{opacity: 0, y: 20}}
            animate={{opacity: 1, y: 0}}
            transition={{duration: 0.6, delay: 0.2}}
            whileHover={{y: -5}}
            whileTap={onClick ? {scale: 0.98} : undefined}
            className={cn(
                "relative overflow-hidden p-6 rounded-3xl border border-white/20 bg-white/40 backdrop-blur-xl shadow-2xl shadow-slate-200/50 group focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none",
                onClick ? "cursor-pointer" : ""
            )}
            onClick={onClick}
            role={onClick ? "button" : undefined}
            tabIndex={onClick ? 0 : undefined}
            aria-label={onClick ? `View market pulse for ${suburb}` : "Market pulse statistics"}
            onKeyDown={
                onClick
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            onClick();
                        }
                    }
                    : undefined
            }
        >
            <div
                className="absolute -top-10 -right-10 w-32 h-32 bg-violet-500/5 blur-3xl rounded-full group-hover:bg-violet-500/10 transition-colors"/>

            <div className="flex justify-between items-start mb-4">
                <div className="p-3 bg-violet-500/10 rounded-2xl">
                    <Activity className="w-6 h-6 text-violet-600"/>
                </div>
                <div className="flex flex-col items-end">
                    <span className="text-xs font-bold uppercase tracking-wider text-slate-400">{suburb}</span>
                    <div className="flex items-center gap-1 text-emerald-500 font-bold text-sm">
                        <ArrowUpRight className="w-4 h-4"/>
                        <span>{growth}</span>
                    </div>
                </div>
            </div>

            <div className="space-y-1 mb-6">
                <h3 className="text-slate-500 text-sm font-medium">Median Unit Price</h3>
                <div className="flex items-baseline gap-2">
          <span className="text-3xl font-black tracking-tight text-slate-900">
            {medianPrice}
          </span>
                    <span className="text-slate-400 font-bold text-[10px] uppercase">ACT 2611</span>
                </div>
            </div>

            <div className="h-20 w-full">
                {/* Sparkline: no axes, no grid, no tooltip — Tremor's SparkAreaChart
                    was chrome-free by definition, so the recharts equivalent has to
                    opt out of all of it explicitly to look the same. */}
                <ResponsiveContainer width="100%" height="100%">
                    <AreaChart
                        data={data && data.length > 0 ? data : chartdata}
                        margin={{top: 0, right: 0, bottom: 0, left: 0}}
                    >
                        <defs>
                            <linearGradient id="marketPulseFill" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stopColor={CHART_SERIES[0]} stopOpacity={0.25}/>
                                <stop offset="100%" stopColor={CHART_SERIES[0]} stopOpacity={0}/>
                            </linearGradient>
                        </defs>
                        <Area
                            type="monotone"
                            dataKey="price"
                            stroke={CHART_SERIES[0]}
                            strokeWidth={2}
                            fill="url(#marketPulseFill)"
                            dot={false}
                            isAnimationActive={false}
                        />
                    </AreaChart>
                </ResponsiveContainer>
            </div>

            <div className="mt-4 pt-4 border-t border-slate-200/50">
                <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest leading-relaxed">
                    Estimated Equity Delta: <span className="text-emerald-600">+$42,000</span> since 2024
                </p>
            </div>
        </motion.div>
    );
};

export default MarketPulseCard;
