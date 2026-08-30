"use client";

import React, {useEffect, useState} from "react";
import {motion} from "framer-motion";
import {CHART_SERIES} from "@/lib/chartTheme";
import {Droplets, Flame, Zap} from "lucide-react";
import {useAuth} from "@/contexts/AuthContext";
import {useActiveUnit} from "@/hooks/useActiveUnit";
/**
 * @generated FunctionHeader
 * Function: UtilityComparisonCard
 * Path: frontend/src/components/dashboard/premium/UtilityComparisonCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const UtilityComparisonCard = ({onClick}: { onClick?: () => void }) => {
    const {api} = useAuth();
    const {activeUnit} = useActiveUnit();
    const [usageData, setUsageData] = useState<any>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchUsage
         * Path: frontend/src/components/dashboard/premium/UtilityComparisonCard.tsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchUsage = async () => {
            if (!activeUnit) return;
            try {
                const res = await api.get(`/analytics/utility-usage/${activeUnit}`);
                setUsageData(res.data);
            } catch (err) {
                console.error("Failed to fetch utility usage:", err);
            } finally {
                setLoading(false);
            }
        };
        fetchUsage();
    }, [api, activeUnit]);

    const chartData = [
        {
            name: "My Unit",
            value: usageData?.electricity_kwh || 450,

            icon: () => <Zap className="w-4 h-4 mr-2 text-indigo-500"/>
        },
        {
            name: "Building Avg",
            value: usageData?.building_avg_kwh || 340,

            icon: () => <Zap className="w-4 h-4 mr-2 text-slate-400"/>
        },
    ];

    const diff = usageData
        ? Math.round(((usageData.electricity_kwh - usageData.building_avg_kwh) / usageData.building_avg_kwh) * 100)
        : 18;

    return (
        <motion.div
            initial={{opacity: 0, scale: 0.95}}
            animate={{opacity: 1, scale: 1}}
            transition={{duration: 0.6, delay: 0.7}}
            whileHover={{y: -5}}
            className={`p-8 rounded-[2rem] border border-white/20 bg-white/40 backdrop-blur-xl shadow-2xl h-full flex flex-col group ${onClick ? 'cursor-pointer' : ''}`}
            onClick={onClick}
        >
            <div className="flex justify-between items-start mb-8">
                <div>
                    <h3 className="text-slate-900 text-xl font-black tracking-tight mb-1">Utility Efficiency</h3>
                    <p className="text-slate-500 text-sm font-medium">Electricity usage vs community average</p>
                </div>
                <div className="p-3 bg-indigo-500/10 rounded-2xl">
                    <Zap className="w-6 h-6 text-indigo-600"/>
                </div>
            </div>

            <div className="flex-1 space-y-8">
                <div>
                    <div className="flex justify-between items-baseline mb-4">
                        <span className="text-[10px] font-black text-slate-400 uppercase tracking-[0.2em]">Current Billing Period</span>
                        <span className={`text-sm font-black ${diff > 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
              {diff > 0 ? '+' : ''}{diff}% vs Avg
            </span>
                    </div>
                    {/* Tremor's BarList has no recharts equivalent — recharts draws
                        plotted charts, and this is a labelled proportional bar list.
                        Rebuilt from the same primitives Tremor used: a track div per
                        row, width as a percentage of the largest value, label over
                        the bar and formatted value to the right. Behaviour matches
                        BarList: bars are relative to the row maximum, not to a fixed
                        scale, so the longest row is always full width. */}
                    <div className="mt-2 space-y-2">
                        {chartData.map((item) => {
                            const max = Math.max(...chartData.map((d) => d.value), 1);
                            const pct = Math.max((item.value / max) * 100, 2); // 2% floor keeps a zero row visible
                            return (
                                <div key={item.name} className="flex items-center gap-3">
                                    <div className="relative h-8 flex-1 overflow-hidden rounded bg-muted">
                                        <div
                                            className="absolute inset-y-0 left-0 rounded"
                                            style={{width: `${pct}%`, backgroundColor: CHART_SERIES[0], opacity: 0.25}}
                                        />
                                        <div className="absolute inset-y-0 left-0 flex items-center gap-1 pl-2">
                                            {item.icon ? item.icon() : null}
                                            <span className="text-xs font-medium text-foreground">{item.name}</span>
                                        </div>
                                    </div>
                                    <span className="w-20 shrink-0 text-right text-xs font-medium text-muted-foreground">
                                        {item.value} kWh
                                    </span>
                                </div>
                            );
                        })}
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                    <div className="p-4 bg-slate-50/50 rounded-2xl border border-slate-100">
                        <Droplets className="w-4 h-4 text-blue-500 mb-2"/>
                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1">Water</p>
                        <p className="text-sm font-black text-slate-900">Normal</p>
                    </div>
                    <div className="p-4 bg-slate-50/50 rounded-2xl border border-slate-100">
                        <Flame className="w-4 h-4 text-orange-500 mb-2"/>
                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1">Gas</p>
                        <p className="text-sm font-black text-slate-900">-5% Low</p>
                    </div>
                </div>
            </div>

            <p className="mt-8 text-[10px] font-medium text-slate-400 leading-relaxed italic">
                "{usageData?.efficiency_note || "Reducing your peak hour usage could save you money this month."}"
            </p>
        </motion.div>
    );
};

export default UtilityComparisonCard;
